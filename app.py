from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
import json
from datetime import datetime

app = Flask(__name__)
app.secret_key = "supersecret"
DB_NAME = "hotel.db"


def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def load_menu_from_json():
    """Loads dishes from menu.json into the database (if it exists)."""
    if not os.path.exists("menu.json"):
        print("Warning: menu.json not found! Dishes will not be loaded.")
        return
    
    try:
        with open("menu.json", "r", encoding="utf-8") as f:
            dishes = json.load(f)
    except json.JSONDecodeError:
        print("Error reading menu.json. Please check file format.")
        return

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM dishes") # Clear old dishes
    
    for dish in dishes:
        c.execute(
            "INSERT INTO dishes (name, description, price, image) VALUES (?, ?, ?, ?)",
            (dish.get("name"), dish.get("description", ""), dish.get("price"), dish.get("image", "default.png"))
        )
    conn.commit()
    conn.close()

def init_db():
    conn = get_db_connection()
    c = conn.cursor()

    # Users: ADDED 'role' COLUMN
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'customer'
                )''')

    # Dishes
    c.execute('''CREATE TABLE IF NOT EXISTS dishes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    price REAL NOT NULL,
                    image TEXT NOT NULL
                )''')

    # Cart
    c.execute('''CREATE TABLE IF NOT EXISTS cart (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    dish_id INTEGER,
                    quantity INTEGER,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(dish_id) REFERENCES dishes(id)
                )''')

    # Orders: UPDATED to include status, delivery_partner, and total_price
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    dish_id INTEGER,
                    dish_name TEXT,
                    quantity INTEGER,
                    total REAL, -- Total for the line item
                    status TEXT NOT NULL DEFAULT 'placed',
                    delivery_partner TEXT,
                    total_price REAL NOT NULL, -- Total for the ENTIRE transaction
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )''')
    conn.commit()

    # Load dishes from json
    load_menu_from_json()

    # Create initial users (Admin/Delivery) if they don't exist
    initial_users = [
        ("admin", "admin@example.com", "adminpass", "admin"),
        ("deliveryman", "delivery@example.com", "deliverpass", "delivery"),
    ]
    for username, email, password, role in initial_users:
        c.execute("SELECT id FROM users WHERE email=?", (email,))
        if c.fetchone() is None:
            try:
                c.execute(
                    "INSERT INTO users (username, email, password, role) VALUES (?, ?, ?, ?)",
                    (username, email, password, role)
                )
                conn.commit()
                print(f"{role.capitalize()} user created: {email}/{password}")
            except sqlite3.IntegrityError:
                pass
            
    conn.close()

# Helper function for Role-Based Access Control (RBAC)
def has_role(required_role):
    return session.get('role') == required_role

# Helper function to check if the user is a staff member
def is_staff():
    return has_role('admin') or has_role('delivery')

# ---------------- CORE APPLICATION ROUTES (Customer/Guest) ----------------

@app.route("/")
def home():
    return render_template("home.html", username=session.get("username"), role=session.get("role"))


@app.route("/menu")
def menu():
    if is_staff():
        flash("Menu access is restricted to customers/guests.", "error")
        return redirect(url_for("home"))
        
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM dishes")
    dishes = c.fetchall()
    conn.close()
    return render_template("menu.html", dishes=dishes, username=session.get("username"), role=session.get("role"))


@app.route("/add_to_cart/<int:dish_id>")
def add_to_cart(dish_id):
    if not session.get("user_id") or is_staff():
        flash("You must be a customer to use the cart.", "error")
        return redirect(url_for("home"))
    
    conn = get_db_connection()
    c = conn.cursor()
    user_id = session["user_id"]

    c.execute("SELECT id, quantity FROM cart WHERE user_id=? AND dish_id=?", (user_id, dish_id))
    cart_item = c.fetchone()

    if cart_item:
        c.execute("UPDATE cart SET quantity = quantity + 1 WHERE id=?", (cart_item["id"],))
    else:
        c.execute("INSERT INTO cart (user_id, dish_id, quantity) VALUES (?, ?, 1)", (user_id, dish_id))
        
    conn.commit()
    conn.close()
    flash("Item added to cart!", "success")
    return redirect(url_for("menu"))


@app.route("/cart")
def cart():
    if not session.get("user_id") or is_staff():
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    conn = get_db_connection()
    c = conn.cursor()
    user_id = session["user_id"]
    
    c.execute("""
        SELECT 
            c.id AS cart_id, 
            d.name, 
            d.price, 
            c.quantity, 
            d.id AS dish_id
        FROM cart c
        JOIN dishes d ON c.dish_id = d.id
        WHERE c.user_id = ?
    """, (user_id,))
    
    cart_items = c.fetchall()
    total = sum(item["price"] * item["quantity"] for item in cart_items)
    
    conn.close()
    # The previous checkout.html/cart.html expect items in this format:
    items = [{
        "cart_id": item["cart_id"],
        "name": item["name"],
        "price": item["price"],
        "quantity": item["quantity"],
        "subtotal": item["price"] * item["quantity"]
    } for item in cart_items]
    
    return render_template("cart.html", items=items, total=total, username=session.get("username"), role=session.get("role"))


@app.route("/remove_from_cart/<int:cart_id>")
def remove_from_cart(cart_id):
    if not session.get("user_id") or is_staff():
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM cart WHERE id=? AND user_id=?", (cart_id, session["user_id"]))
    conn.commit()
    conn.close()
    flash("Item removed from cart.", "info")
    return redirect(url_for("cart"))


@app.route("/details", methods=["GET", "POST"])
def details():
    if not session.get("user_id") or is_staff():
        flash("Access denied.", "error")
        return redirect(url_for("home"))
        
    if request.method == "POST":
        session["customer_name"] = request.form.get("name")
        session["customer_address"] = request.form.get("address")
        session["customer_phone"] = request.form.get("phone")
        return redirect(url_for("checkout"))
        
    return render_template("details.html", username=session.get("username"), role=session.get("role"))


@app.route("/checkout")
def checkout():
    if not session.get("user_id") or is_staff():
        flash("Access denied.", "error")
        return redirect(url_for("home"))
        
    if "customer_name" not in session:
        return redirect(url_for("details"))
        
    conn = get_db_connection()
    c = conn.cursor()
    user_id = session["user_id"]
    
    c.execute("""
        SELECT d.name, d.price, c.quantity
        FROM cart c
        JOIN dishes d ON c.dish_id = d.id
        WHERE c.user_id = ?
    """, (user_id,))
    
    cart_items = c.fetchall()
    total = 0
    items = []
    
    for item in cart_items:
        subtotal = item["price"] * item["quantity"]
        total += subtotal
        items.append({
            "name": item["name"],
            "price": item["price"],
            "quantity": item["quantity"],
            "subtotal": subtotal
        })
        
    conn.close()
    return render_template("checkout.html", items=items, total=total, username=session.get("username"), role=session.get("role"))


@app.route("/payment_gateway")
def payment_gateway():
    if not session.get("user_id") or is_staff():
        flash("Access denied.", "error")
        return redirect(url_for("home"))
        
    if "customer_name" not in session:
        return redirect(url_for("details"))
        
    conn = get_db_connection()
    c = conn.cursor()
    user_id = session["user_id"]
    
    c.execute("""
        SELECT d.name, d.price, c.quantity
        FROM cart c
        JOIN dishes d ON c.dish_id = d.id
        WHERE c.user_id = ?
    """, (user_id,))
    
    cart_items = c.fetchall()
    total = sum(item["price"] * item["quantity"] for item in cart_items)
    
    conn.close()
    # The payment_gateway.html expects items in this format:
    items = [{
        "name": item["name"],
        "quantity": item["quantity"],
        "subtotal": item["price"] * item["quantity"]
    } for item in cart_items]
    
    return render_template("payment_gateway.html", items=items, total=total, username=session.get("username"), role=session.get("role"))


@app.route("/place_order_auto")
def place_order_auto():
    if not session.get("user_id") or is_staff():
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    conn = get_db_connection()
    c = conn.cursor()
    user_id = session["user_id"]
    
    c.execute("SELECT dish_id, quantity FROM cart WHERE user_id=?", (user_id,))
    cart_items = c.fetchall()
    
    if not cart_items:
        flash("Your cart is empty.", "error")
        conn.close()
        return redirect(url_for("cart"))

    # 1. Calculate Grand Total and prepare item list
    grand_total = 0
    dish_items = []
    
    for item in cart_items:
        c.execute("SELECT price, name FROM dishes WHERE id=?", (item["dish_id"],))
        dish = c.fetchone()
        if dish:
            item_total = dish["price"] * item["quantity"]
            grand_total += item_total
            dish_items.append({
                'dish_id': item["dish_id"],
                'dish_name': dish["name"],
                'quantity': item["quantity"],
                'total': item_total
            })
    
    # 2. Get a single timestamp for the entire order
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 3. Insert all line items into the orders table
    first_order_id = None
    for item in dish_items:
        c.execute(
            """
            INSERT INTO orders 
            (user_id, dish_id, dish_name, quantity, total, total_price, timestamp) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, item['dish_id'], item['dish_name'], item['quantity'], 
             item['total'], grand_total, current_time)
        )
        if first_order_id is None:
            first_order_id = c.lastrowid
            
    # 4. Clear the cart
    c.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

    return redirect(url_for("payment_success", order_id=first_order_id))


@app.route("/payment_success")
def payment_success():
    if not session.get("user_id"):
        flash("Please log in first!", "error")
        return redirect(url_for("login"))

    # We use order_id only as a reference to fetch the unique order's details (timestamp and total_price)
    order_id = request.args.get("order_id")
    if not order_id:
        flash("No order found! Returning to home.", "error")
        return redirect(url_for("home"))
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Fetch the unique identifier for this order (total_price and timestamp)
    c.execute("SELECT total_price, status, delivery_partner, timestamp FROM orders WHERE id=?", (order_id,))
    ref_order = c.fetchone()
    
    if not ref_order:
        flash("Order reference not found!", "error")
        conn.close()
        return redirect(url_for("home"))
        
    # Fetch all line items matching the unique identifier
    c.execute("""
        SELECT dish_name, quantity, total 
        FROM orders 
        WHERE total_price = ? AND timestamp = ? AND user_id = ?
        ORDER BY id
    """, (ref_order['total_price'], ref_order['timestamp'], session['user_id']))
    
    line_items = c.fetchall()
    conn.close()

    return render_template(
        "payment_success.html",
        order_id=order_id,
        line_items=line_items,
        total=ref_order['total_price'],
        status=ref_order['status'],
        delivery_partner=ref_order['delivery_partner'],
        timestamp=ref_order['timestamp'],
        customer_name=session.get("customer_name"),
        customer_address=session.get("customer_address"),
        customer_phone=session.get("customer_phone"),
        username=session.get("username"),
        role=session.get("role")
    )
    
# ---------------- AUTHENTICATION & PROFILE ----------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")
        conn = get_db_connection()
        c = conn.cursor()
        try:
            # New users default to 'customer' role
            c.execute("INSERT INTO users (username, email, password, role) VALUES (?, ?, ?, 'customer')", (username, email, password))
            conn.commit()
            flash("Registration successful! Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username or Email already exists!", "error")
        finally:
            conn.close()
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        conn = get_db_connection()
        c = conn.cursor()
        # Select role along with id and username
        c.execute("SELECT id, username, role FROM users WHERE email=? AND password=?", (email, password))
        user = c.fetchone()
        conn.close()
        
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            
            flash(f"Login successful as {user['role'].capitalize()}!", "success")
            
            # Redirect based on the role
            if user["role"] == 'admin':
                return redirect(url_for("admin_dashboard"))
            elif user["role"] == 'delivery':
                return redirect(url_for("delivery_dashboard"))
            
            return redirect(url_for("home"))
        else:
            flash("Invalid email or password!", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully!", "info")
    return redirect(url_for("home"))


@app.route("/profile")
def profile():
    if 'user_id' not in session:
        flash("Please log in to view your profile.", "error")
        return redirect(url_for("login"))
    
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE id=?", (session['user_id'],))
    user = c.fetchone()

    # Fetch all unique orders for the current user
    # Orders are grouped by total_price and timestamp to represent a single transaction
    c.execute("""
        SELECT 
            id, total_price, status, delivery_partner, timestamp
        FROM orders
        WHERE user_id=?
        GROUP BY total_price, timestamp, delivery_partner, status 
        ORDER BY timestamp DESC
    """, (session['user_id'],))
    
    order_summaries = c.fetchall() 
    
    conn.close()
    
    return render_template("profile.html", 
        user=user, 
        orders=order_summaries,
        username=session.get("username"),
        role=session.get("role")
    )

# ---------------- ADMIN ROUTES (Order & Menu Management) ----------------

@app.route("/admin")
def admin_dashboard():
    if not has_role('admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("login"))
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # 1. Fetch all unique orders (grouped by transaction identifier)
    c.execute("""
        SELECT 
            id, user_id, total_price, status, delivery_partner, timestamp
        FROM orders 
        GROUP BY total_price, timestamp, delivery_partner, status
        ORDER BY timestamp DESC
    """)
    orders = c.fetchall()

    # 2. Fetch all delivery partners
    c.execute("SELECT username FROM users WHERE role='delivery'")
    delivery_list = c.fetchall()
    
    # 3. Enhance orders with customer name
    enhanced_orders = []
    for order in orders:
        c.execute("SELECT username FROM users WHERE id=?", (order['user_id'],))
        customer_result = c.fetchone()
        customer_username = customer_result['username'] if customer_result else 'Unknown User'

        enhanced_orders.append({
            'id': order['id'], # This is the first order item ID, used as a reference
            'customer_username': customer_username,
            'total': order['total_price'],
            'status': order['status'],
            'delivery_partner': order['delivery_partner'],
            'timestamp': order['timestamp']
        })
    
    conn.close()
    
    return render_template("admin_orders.html", 
        orders=enhanced_orders, 
        delivery_list=delivery_list,
        username=session.get("username"),
        role=session.get("role")
    )

@app.route("/admin/menu", methods=["GET", "POST"])
def admin_menu():
    if not has_role('admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("login"))

    conn = get_db_connection()
    c = conn.cursor()
    
    if request.method == "POST":
        name = request.form.get("name")
        price = request.form.get("price")
        description = request.form.get("description", "")
        image = request.form.get("image", "default.png") 
        
        try:
            c.execute(
                "INSERT INTO dishes (name, price, description, image) VALUES (?, ?, ?, ?)",
                (name, float(price), description, image)
            )
            conn.commit()
            flash(f"Dish '{name}' added successfully!", "success")
        except ValueError:
            flash("Invalid price.", "error")
        except sqlite3.Error as e:
            flash(f"Database error: {e}", "error")

    c.execute("SELECT * FROM dishes ORDER BY name")
    dishes = c.fetchall()
    conn.close()
    
    return render_template("admin_menu.html", 
        dishes=dishes,
        username=session.get("username"),
        role=session.get("role")
    )

@app.route("/admin/delete_dish/<int:dish_id>")
def admin_delete_dish(dish_id):
    if not has_role('admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("login"))
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM dishes WHERE id=?", (dish_id,))
    conn.commit()
    conn.close()
    flash("Dish deleted successfully.", "info")
    return redirect(url_for("admin_menu"))

@app.route("/admin/assign/<int:order_id>", methods=["POST"])
def admin_assign(order_id):
    if not has_role('admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("login"))

    delivery_user = request.form.get("delivery_user")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Get the unique identifier for the order using the reference ID
    c.execute("SELECT total_price, timestamp FROM orders WHERE id=?", (order_id,))
    ref_order = c.fetchone()
    
    if ref_order:
        # Update ALL line items belonging to the same unique order
        c.execute(
            """
            UPDATE orders 
            SET delivery_partner = ?, status = 'preparing' 
            WHERE total_price = ? AND timestamp = ?
            """,
            (delivery_user, ref_order['total_price'], ref_order['timestamp'])
        )
        conn.commit()
        flash(f"Order #{order_id} assigned to {delivery_user} and status set to 'Preparing'.", "success")
    else:
        flash("Order not found.", "error")
        
    conn.close()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/update_status/<int:order_id>", methods=["POST"])
def admin_update_status(order_id):
    if not has_role('admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("login"))

    new_status = request.form.get("status")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Get the unique identifier for the order using the reference ID
    c.execute("SELECT total_price, timestamp FROM orders WHERE id=?", (order_id,))
    ref_order = c.fetchone()
    
    if ref_order:
        # Update ALL line items belonging to the same unique order
        c.execute(
            """
            UPDATE orders 
            SET status = ? 
            WHERE total_price = ? AND timestamp = ?
            """,
            (new_status, ref_order['total_price'], ref_order['timestamp'])
        )
        conn.commit()
        flash(f"Order #{order_id} status updated to {new_status}.", "success")
    else:
        flash("Order not found.", "error")
        
    conn.close()
    return redirect(url_for("admin_dashboard"))


# ---------------- DELIVERY ROUTES ----------------

@app.route("/delivery")
def delivery_dashboard():
    if not has_role('delivery'):
        flash("Delivery access required.", "error")
        return redirect(url_for("login"))
    
    conn = get_db_connection()
    c = conn.cursor()
    
    delivery_user = session['username']
    
    # Fetch unique orders assigned to this delivery partner
    c.execute("""
        SELECT 
            o.id, 
            u.username AS customer_username, 
            o.total_price, 
            o.status, 
            o.timestamp
        FROM orders o
        JOIN users u ON o.user_id = u.id
        WHERE o.delivery_partner = ?
        GROUP BY o.total_price, o.timestamp, o.status
        ORDER BY o.timestamp DESC
    """, (delivery_user,))
    assigned_orders = c.fetchall()
    
    conn.close()
    
    return render_template("delivery_dashboard.html", 
        orders=assigned_orders,
        username=session.get("username"),
        role=session.get("role")
    )
    
@app.route("/delivery/update_status/<int:order_id>", methods=["POST"])
def delivery_update_status(order_id):
    if not has_role('delivery'):
        flash("Delivery access required.", "error")
        return redirect(url_for("login"))

    new_status = request.form.get("status")
    delivery_user = session['username']
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Get the unique identifier for the order and ensure it's assigned to the current user
    c.execute("SELECT total_price, timestamp FROM orders WHERE id=? AND delivery_partner=?", (order_id, delivery_user))
    ref_order = c.fetchone()
    
    if ref_order:
        # Update ALL line items belonging to the same unique order
        c.execute(
            """
            UPDATE orders 
            SET status = ? 
            WHERE total_price = ? AND timestamp = ? AND delivery_partner = ?
            """,
            (new_status, ref_order['total_price'], ref_order['timestamp'], delivery_user)
        )
        conn.commit()
        flash(f"Order #{order_id} status updated to {new_status}.", "success")
    else:
        flash("Order not found or not assigned to you.", "error")
        
    conn.close()
    return redirect(url_for("delivery_dashboard"))


# ---------------- MAIN ----------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True)