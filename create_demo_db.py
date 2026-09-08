"""
Creates the demo SQLite database (demo.db): a small but realistic online-store
schema so the natural-language-to-SQL chatbot has something rich to query.

Eight tables — categories, customers, products, orders, order_items, reviews,
shipments, returns. Every date is anchored to date.today() and order volume is
seasonally weighted (holiday spike, weekend lift), so questions like "revenue
last month" or "return rate this quarter" always land on real data, whenever
this runs.

Run: python create_demo_db.py
"""
import random
import sqlite3
from datetime import date, timedelta

DB_PATH = "demo.db"
SEED = 42  # fixed so the data shape is reproducible across runs / deploys

SCHEMA = """
DROP TABLE IF EXISTS returns;
DROP TABLE IF EXISTS shipments;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS customers;

CREATE TABLE categories (
    category_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    department    TEXT NOT NULL,
    target_margin REAL NOT NULL
);

CREATE TABLE customers (
    customer_id         INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    email               TEXT NOT NULL,
    city                TEXT,
    country             TEXT,
    acquisition_channel TEXT,
    birth_date          DATE,
    is_business         INTEGER NOT NULL DEFAULT 0,
    signup_date         DATE
);

CREATE TABLE products (
    product_id   INTEGER PRIMARY KEY,
    category_id  INTEGER NOT NULL REFERENCES categories(category_id),
    product_name TEXT NOT NULL,
    brand        TEXT,
    unit_price   REAL NOT NULL,
    cost         REAL NOT NULL,
    launch_date  DATE,
    is_active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE orders (
    order_id       INTEGER PRIMARY KEY,
    customer_id    INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date     DATE NOT NULL,
    status         TEXT NOT NULL,
    channel        TEXT,
    payment_method TEXT,
    shipping_cost  REAL NOT NULL DEFAULT 0,
    tax            REAL NOT NULL DEFAULT 0,
    promo_code     TEXT
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),
    product_id    INTEGER NOT NULL REFERENCES products(product_id),
    quantity      INTEGER NOT NULL,
    unit_price    REAL NOT NULL,
    discount      REAL NOT NULL DEFAULT 0
);

CREATE TABLE reviews (
    review_id   INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(product_id),
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    rating      INTEGER NOT NULL,
    review_date DATE NOT NULL
);

CREATE TABLE shipments (
    shipment_id    INTEGER PRIMARY KEY,
    order_id       INTEGER NOT NULL REFERENCES orders(order_id),
    carrier        TEXT NOT NULL,
    shipped_date   DATE,
    delivered_date DATE,
    status         TEXT NOT NULL
);

CREATE TABLE returns (
    return_id     INTEGER PRIMARY KEY,
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id),
    return_date   DATE NOT NULL,
    reason        TEXT NOT NULL,
    refund_amount REAL NOT NULL
);
"""

# name, department, target_margin
CATEGORIES = [
    ("Laptops & Computers", "Electronics", 0.18),
    ("Audio", "Electronics", 0.30),
    ("Phone Accessories", "Electronics", 0.45),
    ("Furniture", "Home & Kitchen", 0.35),
    ("Kitchen", "Home & Kitchen", 0.38),
    ("Fitness", "Sporting Goods", 0.40),
    ("Books", "Media", 0.22),
    ("Apparel", "Apparel", 0.55),
]

# category name -> (brands, item names, (price_low, price_high))
CATALOG = {
    "Laptops & Computers": (
        ["Nimbus", "Corvid", "Vantage", "PearCore"],
        ['13" Ultrabook', '15" Laptop', "Desktop Tower", "2-in-1 Convertible", "Mini PC", "Gaming Laptop", "Chromebook"],
        (399, 2100),
    ),
    "Audio": (
        ["Rumble", "Aera", "SoundPost", "Nimbus"],
        ["Wireless Earbuds", "Over-Ear Headphones", "Bluetooth Speaker", "Soundbar", "Studio Monitor", "Turntable"],
        (29, 399),
    ),
    "Phone Accessories": (
        ["Aera", "GripKit", "Voltway"],
        ["Silicone Case", "Screen Protector", "USB-C Cable", "20W Charger", "MagSafe Wallet", "Car Mount", "Power Bank"],
        (8, 79),
    ),
    "Furniture": (
        ["Oakhaus", "Studio9", "Vantage"],
        ["Standing Desk", "Task Chair", "Bookshelf", "Bed Frame", "Console Table", "Filing Cabinet"],
        (89, 749),
    ),
    "Kitchen": (
        ["Copperline", "Studio9", "Oakhaus"],
        ["Drip Coffee Maker", "Espresso Machine", "Chef's Knife", "Cast Iron Skillet", "Blender", "Air Fryer", "Electric Kettle"],
        (19, 329),
    ),
    "Fitness": (
        ["Kinetic", "Summit", "Rumble"],
        ["Yoga Mat", "Adjustable Dumbbells", "Resistance Bands", "Running Shoes", "Foam Roller", "Jump Rope", "Kettlebell"],
        (12, 349),
    ),
    "Books": (
        [None],
        ["Novel: The Long Winter", "Cookbook: Fast Weeknights", "Sci-Fi: Orbital Decay", "Biography: The Builder",
         "Kids: Where the Foxes Go", "Nonfiction: Deep Work Habits", "Poetry: Salt and Light"],
        (9, 32),
    ),
    "Apparel": (
        ["Northbound", "GripKit", "Summit"],
        ["Denim Jacket", "Merino Sweater", "Rain Shell", "Chino Trousers", "Graphic Tee", "Wool Socks 3-Pack", "Fleece Hoodie"],
        (14, 189),
    ),
}

FIRST_NAMES = [
    "Alex", "Jordan", "Sam", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Avery", "Quinn",
    "Devon", "Skyler", "Reese", "Rowan", "Hayden", "Emerson", "Parker", "Sawyer", "Blake", "Drew",
    "Noa", "Kai", "Luca", "Mira", "Priya", "Diego", "Yuki", "Omar", "Ingrid", "Sofia",
]
LAST_NAMES = [
    "Chen", "Patel", "Garcia", "Smith", "Nguyen", "Brown", "Kim", "Rossi", "Okafor", "Muller",
    "Silva", "Haddad", "Novak", "Ivanov", "Andersson", "Costa", "Reyes", "Fischer", "Walsh", "Tanaka",
]
PLACES = [
    ("New York", "USA"), ("Los Angeles", "USA"), ("Chicago", "USA"), ("Austin", "USA"),
    ("Seattle", "USA"), ("Denver", "USA"), ("Miami", "USA"), ("Boston", "USA"),
    ("Toronto", "Canada"), ("Vancouver", "Canada"), ("London", "UK"), ("Manchester", "UK"),
    ("Berlin", "Germany"), ("Munich", "Germany"), ("Sydney", "Australia"), ("Dublin", "Ireland"),
]
ACQUISITION = ["organic", "paid_search", "referral", "social", "email"]
CHANNELS = ["web", "mobile", "in_store"]
PAYMENTS = ["credit_card", "paypal", "apple_pay", "gift_card"]
PROMOS = ["WELCOME10", "SPRING15", "FREESHIP", "VIP20", "BLACKFRIDAY"]
CARRIERS = ["UPS", "FedEx", "USPS", "DHL"]
RETURN_REASONS = ["wrong_size", "defective", "not_as_described", "changed_mind", "arrived_late", "found_better_price"]

N_CUSTOMERS = 300
N_ORDERS = 3500
ORDER_WINDOW_DAYS = 550  # ~18 months


def _season_weight(d: date) -> float:
    """Relative likelihood of an order falling on date d."""
    w = 1.0
    if d.month in (11, 12):
        w *= 1.8          # holiday shopping
    elif d.month == 1:
        w *= 0.8          # post-holiday lull
    elif d.month in (6, 7):
        w *= 1.15         # summer bump
    if d.weekday() >= 5:
        w *= 1.3          # weekend lift
    return w


def build_demo_db():
    random.seed(SEED)
    today = date.today()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(SCHEMA)

    # ---- categories ------------------------------------------------------
    category_rows = [(i, name, dept, margin) for i, (name, dept, margin) in enumerate(CATEGORIES, start=1)]

    # ---- products ------------------------------------------------------
    products = []
    pid = 0
    for cat_id, (cname, _dept, margin) in enumerate(CATEGORIES, start=1):
        brands, items, (lo, hi) = CATALOG[cname]
        combos = [(b, it) for it in items for b in brands]
        random.shuffle(combos)
        for brand, item in combos[:11]:
            pid += 1
            price = round(random.uniform(lo, hi) - 0.01, 2)
            cost = round(price * (1 - margin) * random.uniform(0.9, 1.08), 2)
            cost = min(cost, round(price * 0.95, 2))
            launch = today - timedelta(days=random.randint(20, 1400))
            is_active = 0 if random.random() < 0.10 else 1
            name = item if brand is None else f"{brand} {item}"
            products.append((pid, cat_id, name, brand, price, cost, launch.isoformat(), is_active))

    # ---- customers ------------------------------------------------------
    customers = []
    for i in range(1, N_CUSTOMERS + 1):
        fn, ln = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        city, country = random.choice(PLACES)
        if random.random() < 0.05:
            city = None  # realistic missing value
        age_years = random.randint(19, 68)
        birth = today - timedelta(days=age_years * 365 + random.randint(0, 364))
        is_business = 1 if random.random() < 0.15 else 0
        if random.random() < 0.06:
            signup = today - timedelta(days=random.randint(1, 30))       # fresh signups
        else:
            signup = today - timedelta(days=random.randint(31, 1100))    # older base
        acq = random.choices(ACQUISITION, weights=[40, 25, 15, 12, 8])[0]
        email = f"{fn.lower()}.{ln.lower()}{i}@example.com"
        customers.append((i, f"{fn} {ln}", email, city, country, acq,
                          birth.isoformat(), is_business, signup.isoformat()))

    cust_ids = [c[0] for c in customers]
    cust_signup = {c[0]: date.fromisoformat(c[8]) for c in customers}
    cust_weights = []
    for _ in cust_ids:
        r = random.random()
        cust_weights.append(12 if r < 0.05 else 4 if r < 0.20 else 1)  # a few whales, a long tail

    prod_ids = [p[0] for p in products]
    prod_weights = []
    for _ in prod_ids:
        r = random.random()
        prod_weights.append(8 if r < 0.10 else 3 if r < 0.35 else 1)   # bestsellers vs the rest

    candidate_dates = [today - timedelta(days=d) for d in range(ORDER_WINDOW_DAYS)]
    date_weights = [_season_weight(d) for d in candidate_dates]

    # ---- orders + order_items ----------------------------------------
    # Pick the order date first (seasonally weighted), then assign a customer
    # who had already signed up by then. This keeps the monthly distribution
    # driven by seasonality rather than by how the customer base grew.
    orders = []
    items = []
    oid = iid = 0
    for _ in range(N_ORDERS):
        oid += 1
        odate = random.choices(candidate_dates, weights=date_weights, k=1)[0]
        eligible = [cid for cid in cust_ids if cust_signup[cid] <= odate]
        if not eligible:
            eligible = cust_ids
        eligible_weights = [cust_weights[cid - 1] for cid in eligible]
        cid = random.choices(eligible, weights=eligible_weights, k=1)[0]

        age_days = (today - odate).days
        if age_days <= 3:
            status = random.choices(["pending", "processing"], weights=[70, 30])[0]
        elif age_days <= 12:
            status = random.choices(["processing", "completed", "cancelled"], weights=[35, 55, 10])[0]
        else:
            status = random.choices(["completed", "cancelled", "refunded"], weights=[86, 9, 5])[0]

        channel = random.choices(CHANNELS, weights=[55, 35, 10])[0]
        payment = random.choices(PAYMENTS, weights=[60, 22, 13, 5])[0]

        promo = None
        if odate.month == 11 and random.random() < 0.35:
            promo = "BLACKFRIDAY"
        elif random.random() < 0.18:
            promo = random.choice(PROMOS)

        n_items = random.choices([1, 2, 3, 4, 5], weights=[42, 30, 16, 8, 4])[0]
        subtotal = 0.0
        picked = set()
        for _ in range(n_items):
            prod_id = random.choices(prod_ids, weights=prod_weights, k=1)[0]
            if prod_id in picked:
                continue
            picked.add(prod_id)
            list_price = products[prod_id - 1][4]
            sale_price = round(list_price * random.uniform(0.90, 1.04), 2)  # price drifts over time
            qty = random.choices([1, 2, 3], weights=[75, 18, 7])[0]
            line = sale_price * qty
            discount = round(line * random.uniform(0.05, 0.30), 2) if random.random() < 0.22 else 0.0
            subtotal += line - discount
            iid += 1
            items.append((iid, oid, prod_id, qty, sale_price, discount))

        if promo == "FREESHIP" or subtotal > 150 or channel == "in_store":
            shipping = 0.0
        else:
            shipping = random.choice([4.99, 6.99, 8.99, 12.99])
        tax = round(subtotal * random.choice([0.0, 0.06, 0.0725, 0.08, 0.095]), 2)
        orders.append((oid, cid, odate.isoformat(), status, channel, payment, shipping, tax, promo))

    order_customer = {o[0]: o[1] for o in orders}

    # ---- shipments ---------------------------------------------------
    shipments = []
    sid = 0
    delivered_on = {}
    for oid_, cid, odate_s, status, *_rest in orders:
        odate = date.fromisoformat(odate_s)
        if status == "cancelled":
            continue
        if status == "refunded" and random.random() < 0.5:
            continue  # refunded before it ever shipped
        if random.random() < 0.03:
            continue  # a few orders with no shipment record yet
        sid += 1
        carrier = random.choices(CARRIERS, weights=[35, 30, 25, 10])[0]
        shipped = odate + timedelta(days=random.randint(1, 4))
        if shipped > today:
            shipments.append((sid, oid_, carrier, None, None, "label_created"))
            continue
        transit = random.randint(2, 7)
        if random.random() < 0.10:
            transit += random.randint(4, 12)  # delayed shipment
        delivered = shipped + timedelta(days=transit)
        if delivered > today:
            shipments.append((sid, oid_, carrier, shipped.isoformat(), None, "in_transit"))
        else:
            shipments.append((sid, oid_, carrier, shipped.isoformat(), delivered.isoformat(), "delivered"))
            delivered_on[oid_] = delivered

    # ---- reviews ---------------------------------------------------
    reviews = []
    rid = 0
    seen_product_customer = set()
    for iid_, oid_, prod_id, qty, sale_price, discount in items:
        if oid_ not in delivered_on or random.random() > 0.28:
            continue
        cid = order_customer[oid_]
        if (prod_id, cid) in seen_product_customer:
            continue
        seen_product_customer.add((prod_id, cid))
        rid += 1
        rating = random.choices([5, 4, 3, 2, 1], weights=[46, 29, 12, 8, 5])[0]
        review_date = min(today, delivered_on[oid_] + timedelta(days=random.randint(2, 40)))
        reviews.append((rid, prod_id, cid, rating, review_date.isoformat()))

    # ---- returns ---------------------------------------------------
    returns_rows = []
    ret_id = 0
    for iid_, oid_, prod_id, qty, sale_price, discount in items:
        if oid_ not in delivered_on or random.random() > 0.06:
            continue
        ret_id += 1
        return_date = min(today, delivered_on[oid_] + timedelta(days=random.randint(1, 25)))
        reason = random.choices(RETURN_REASONS, weights=[28, 22, 16, 20, 8, 6])[0]
        line_total = round(sale_price * qty - discount, 2)
        refund = round(line_total * random.uniform(0.75, 1.0), 2)
        returns_rows.append((ret_id, iid_, return_date.isoformat(), reason, refund))

    # ---- insert ---------------------------------------------------
    cur.executemany("INSERT INTO categories VALUES (?,?,?,?)", category_rows)
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?)", customers)
    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", products)
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", orders)
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)
    cur.executemany("INSERT INTO reviews VALUES (?,?,?,?,?)", reviews)
    cur.executemany("INSERT INTO shipments VALUES (?,?,?,?,?,?)", shipments)
    cur.executemany("INSERT INTO returns VALUES (?,?,?,?,?)", returns_rows)

    conn.commit()
    conn.close()

    print(f"Demo database created at {DB_PATH}")
    for label, rows in [
        ("categories", category_rows), ("customers", customers), ("products", products),
        ("orders", orders), ("order_items", items), ("reviews", reviews),
        ("shipments", shipments), ("returns", returns_rows),
    ]:
        print(f"  {label:12s}: {len(rows)}")


if __name__ == "__main__":
    build_demo_db()
