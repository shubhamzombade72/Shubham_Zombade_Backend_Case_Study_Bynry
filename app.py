import os
from flask import Flask, request, jsonify
from decimal import Decimal
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from models import db, Company, Warehouse, ProductType, Product, Inventory, InventoryLog, Supplier, ProductSupplier

app = Flask(__name__)
DB_PATH = os.path.join(os.getcwd(), 'stockflow.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{DB_PATH}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

@app.route('/api/products', methods=['POST'])
def create_product():
    data = request.json or {}

    # Basic fields from the original prompt
    required_fields = ['name', 'sku', 'price', 'warehouse_id']
    missing = [f for f in required_fields if f not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    # These fields were added in my Part 2 Schema Design.
    # For testing, we default to the first available if not provided.
    company_id = data.get('company_id', 1)
    product_type_id = data.get('product_type_id', 1)

    try:
        # Check if SKU exists
        if Product.query.filter_by(sku=data['sku']).first():
            return jsonify({"error": "SKU already exists"}), 409

        # Single transaction for atomicity
        product = Product(
            company_id=company_id,
            product_type_id=product_type_id,
            name=data['name'],
            sku=data['sku'],
            price=Decimal(str(data['price']))
        )
        db.session.add(product)
        db.session.flush() # Get product.id

        inventory = Inventory(
            product_id=product.id,
            warehouse_id=data['warehouse_id'],
            quantity=data.get('initial_quantity', 0)
        )
        db.session.add(inventory)
        
        db.session.commit()

        return jsonify({
            "message": "Product created successfully",
            "product_id": product.id
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Database integrity error"}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/companies/<int:company_id>/alerts/low-stock', methods=['GET'])
def get_low_stock_alerts(company_id):
    recent_window = datetime.utcnow() - timedelta(days=30)

    # Main query based on schema design
    results = db.session.query(
        Product,
        Warehouse,
        Inventory.quantity,
        ProductType.default_threshold,
        Supplier
    ).join(Warehouse, Warehouse.company_id == Product.company_id) \
     .join(Inventory, (Inventory.product_id == Product.id) & (Inventory.warehouse_id == Warehouse.id)) \
     .join(ProductType, ProductType.id == Product.product_type_id) \
     .join(ProductSupplier, ProductSupplier.product_id == Product.id) \
     .join(Supplier, Supplier.id == ProductSupplier.supplier_id) \
     .filter(Product.company_id == company_id) \
     .filter(ProductSupplier.is_primary == True) \
     .all()

    alerts = []
    for product, warehouse, current_stock, threshold, supplier in results:
        # Check if stock is low
        if current_stock >= threshold:
            continue

        # Check recent sales velocity
        recent_sales_sum = db.session.query(func.sum(InventoryLog.change_amount)) \
            .filter(InventoryLog.product_id == product.id) \
            .filter(InventoryLog.reason == 'sale') \
            .filter(InventoryLog.created_at >= recent_window) \
            .scalar() or 0
        
        recent_sales_abs = abs(recent_sales_sum)
        if recent_sales_abs == 0:
            continue

        avg_daily_sales = recent_sales_abs / 30
        days_until_stockout = int(current_stock / avg_daily_sales) if avg_daily_sales > 0 else 999

        alerts.append({
            "product_id": product.id,
            "product_name": product.name,
            "sku": product.sku,
            "warehouse_id": warehouse.id,
            "warehouse_name": warehouse.name,
            "current_stock": current_stock,
            "threshold": threshold,
            "days_until_stockout": days_until_stockout,
            "supplier": {
                "id": supplier.id,
                "name": supplier.name,
                "contact_email": supplier.contact_email
            }
        })

    return jsonify({
        "alerts": alerts,
        "total_alerts": len(alerts)
    }), 200

@app.route('/api/seed', methods=['POST'])
def seed_data():
    db.drop_all()
    db.create_all()

    company = Company(name="Acme Corp")
    db.session.add(company)
    db.session.flush()

    warehouse = Warehouse(company_id=company.id, name="Main Hub")
    db.session.add(warehouse)
    
    pt = ProductType(name="Electronics", default_threshold=20)
    db.session.add(pt)
    db.session.flush()

    supplier = Supplier(name="Global Tech", contact_email="orders@globaltech.com")
    db.session.add(supplier)
    db.session.flush()

    # Product that should trigger an alert (stock 10, threshold 20, recent sales)
    p1 = Product(company_id=company.id, product_type_id=pt.id, sku="SKU-001", name="Widget A", price=Decimal("19.99"))
    db.session.add(p1)
    db.session.flush()

    inv1 = Inventory(product_id=p1.id, warehouse_id=warehouse.id, quantity=10)
    db.session.add(inv1)
    
    ps1 = ProductSupplier(product_id=p1.id, supplier_id=supplier.id, is_primary=True)
    db.session.add(ps1)

    # Add some sales logs for P1
    log1 = InventoryLog(product_id=p1.id, warehouse_id=warehouse.id, change_amount=-5, reason='sale')
    db.session.add(log1)

    # Product that should NOT trigger an alert (plenty of stock)
    p2 = Product(company_id=company.id, product_type_id=pt.id, sku="SKU-002", name="Gadget B", price=Decimal("49.99"))
    db.session.add(p2)
    db.session.flush()

    inv2 = Inventory(product_id=p2.id, warehouse_id=warehouse.id, quantity=100)
    db.session.add(inv2)

    db.session.commit()
    return jsonify({"message": "Database seeded with test data", "company_id": company.id}), 201

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)
