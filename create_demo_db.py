"""
Creates a small demo SQLite database (demo.db) with a simple sales schema
so the natural-language-to-SQL chatbot has something to query out of the box.

Run: python create_demo_db.py
"""
import sqlite3
import random
from datetime import date, timedelta

DB_PATH = "demo.db"

SCHEMA = """
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS products;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    city TEXT,
    signup_date DATE
);

CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    category TEXT,
    unit_price REAL NOT NULL
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    order_date DATE NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
"""

CITIES = ["Miami", "New York", "Austin", "Chicago", "Seattle", "Denver"]
CATEGORIES = ["Electronics", "Home", "Sporting Goods", "Books", "Apparel"]
STATUSES = ["completed", "pending", "cancelled", "refunded"]

PRODUCT_NAMES = [
    ("Wireless Mouse", "Electronics", 24.99),
    ("Mechanical Keyboard", "Electronics", 89.99),
    ("Standing Desk", "Home", 349.00),
    ("Yoga Mat", "Sporting Goods", 29.99),
    ("Running Shoes", "Sporting Goods", 74.50),
    ("Novel: The Long Winter", "Books", 14.99),
    ("Cookbook: Fast Weeknights", "Books", 22.00),
    ("Denim Jacket", "Apparel", 59.99),
    ("Wool Sweater", "Apparel", 64.99),
    ("Coffee Maker", "Home", 45.00),
    ("Desk Lamp", "Home", 19.99),
    ("Bluetooth Speaker", "Electronics", 39.99),
]


def build_demo_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(SCHEMA)

    # Dates are anchored to "today" so relative questions ("last month",
    # "this year") always land on real data, wherever/whenever this runs.
    today = date.today()
    ORDER_WINDOW_DAYS = 548  # ~18 months

    # Customers
    first_names = ["Alex", "Jordan", "Sam", "Taylor", "Morgan", "Casey", "Riley", "Jamie"]
    last_names = ["Chen", "Patel", "Garcia", "Smith", "Nguyen", "Brown", "Kim", "Rossi"]
    customers = []
    for i in range(1, 31):
        fn, ln = random.choice(first_names), random.choice(last_names)
        # signed up sometime before the order window opens
        signup = today - timedelta(days=random.randint(ORDER_WINDOW_DAYS, ORDER_WINDOW_DAYS + 600))
        customers.append((i, f"{fn} {ln}", f"{fn.lower()}.{ln.lower()}{i}@example.com",
                           random.choice(CITIES), signup.isoformat()))
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    # Products
    products = [(i + 1, name, cat, price) for i, (name, cat, price) in enumerate(PRODUCT_NAMES)]
    cur.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    # Orders + order_items
    order_id = 1
    item_id = 1
    orders = []
    items = []
    for _ in range(120):
        cust_id = random.randint(1, 30)
        order_date = today - timedelta(days=random.randint(0, ORDER_WINDOW_DAYS))
        status = random.choices(STATUSES, weights=[70, 15, 10, 5])[0]
        orders.append((order_id, cust_id, order_date.isoformat(), status))

        for _ in range(random.randint(1, 4)):
            prod_id = random.randint(1, len(PRODUCT_NAMES))
            qty = random.randint(1, 3)
            items.append((item_id, order_id, prod_id, qty))
            item_id += 1
        order_id += 1

    cur.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?)", items)

    conn.commit()
    conn.close()
    print(f"Demo database created at {DB_PATH}")
    print(f"  customers: {len(customers)}")
    print(f"  products: {len(products)}")
    print(f"  orders: {len(orders)}")
    print(f"  order_items: {len(items)}")


if __name__ == "__main__":
    build_demo_db()
