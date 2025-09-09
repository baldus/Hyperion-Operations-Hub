import csv
import io
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from invapp.models import db, Item, Location, Stock

bp = Blueprint("inventory", __name__, url_prefix="/inventory")

############################
# INVENTORY HOME
############################

@bp.route("/")
def inventory_home():
    """Inventory home page with actions menu"""
    return render_template("inventory/home.html")

############################
# ITEM ROUTES
############################

@bp.route("/items")
def list_items():
    items = Item.query.all()
    return render_template("inventory/list.html", items=items)

@bp.route("/item/new", methods=["GET", "POST"])
def create_item():
    if request.method == "POST":
        sku = request.form["sku"]
        name = request.form["name"]
        unit = request.form.get("unit", "ea")
        description = request.form.get("description", "")
        min_stock = request.form.get("min_stock", 0)

        item = Item(sku=sku, name=name, unit=unit, description=description, min_stock=min_stock)
        db.session.add(item)
        db.session.commit()
        flash("Item created successfully", "success")
        return redirect(url_for("inventory.list_items"))

    return render_template("inventory/form_item.html")

@bp.route("/item/import", methods=["GET", "POST"])
def import_item():
    if request.method == "POST":
        file = request.files["file"]
        if not file:
            flash("No file uploaded", "danger")
            return redirect(request.url)

        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)

        for row in csv_input:
            item = Item(
                sku=row["sku"],
                name=row["name"],
                unit=row.get("unit", "ea"),
                description=row.get("description", ""),
                min_stock=row.get("min_stock", 0),
            )
            db.session.add(item)
        db.session.commit()
        flash("Items imported successfully", "success")
        return redirect(url_for("inventory.list_items"))

    return render_template("inventory/import_items.html")

@bp.route("/item/export")
def export_item():
    items = Item.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "sku", "name", "unit", "description", "min_stock"])
    for i in items:
        writer.writerow([i.id, i.sku, i.name, i.unit, i.description, i.min_stock])
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=items.csv"
    return response


############################
# LOCATION ROUTES
############################

@bp.route("/locations")
def list_locations():
    locations = Location.query.all()
    return render_template("inventory/locations.html", locations=locations)

@bp.route("/location/new", methods=["GET", "POST"])
def create_location():
    if request.method == "POST":
        code = request.form["code"]
        description = request.form.get("description", "")

        loc = Location(code=code, description=description)
        db.session.add(loc)
        db.session.commit()
        flash("Location created successfully", "success")
        return redirect(url_for("inventory.list_locations"))

    return render_template("inventory/add_location.html")

@bp.route("/location/import", methods=["GET", "POST"])
def import_location():
    if request.method == "POST":
        file = request.files["file"]
        if not file:
            flash("No file uploaded", "danger")
            return redirect(request.url)

        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)

        for row in csv_input:
            loc = Location(
                code=row["code"],
                description=row.get("description", "")
            )
            db.session.add(loc)
        db.session.commit()
        flash("Locations imported successfully", "success")
        return redirect(url_for("inventory.list_locations"))

    return render_template("inventory/import_locations.html")

@bp.route("/location/export")
def export_location():
    locations = Location.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "code", "description"])
    for l in locations:
        writer.writerow([l.id, l.code, l.description])
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=locations.csv"
    return response


############################
# STOCK ROUTES
############################

@bp.route("/stock")
def list_stock():
    stocks = Stock.query.all()
    return render_template("inventory/stock.html", stocks=stocks)

@bp.route("/stock/new", methods=["GET", "POST"])
def create_stock():
    if request.method == "POST":
        item_id = request.form["item_id"]
        location_id = request.form["location_id"]
        quantity = request.form.get("quantity", 0)

        stock = Stock(item_id=item_id, location_id=location_id, quantity=quantity)
        db.session.add(stock)
        db.session.commit()
        flash("Stock created successfully", "success")
        return redirect(url_for("inventory.list_stock"))

    return render_template("inventory/add_stock.html")

@bp.route("/stock/import", methods=["GET", "POST"])
def import_stock():
    if request.method == "POST":
        file = request.files["file"]
        if not file:
            flash("No file uploaded", "danger")
            return redirect(request.url)

        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)

        for row in csv_input:
            stock = Stock(
                item_id=row["item_id"],
                location_id=row["location_id"],
                quantity=row.get("quantity", 0)
            )
            db.session.add(stock)
        db.session.commit()
        flash("Stock imported successfully", "success")
        return redirect(url_for("inventory.list_stock"))

    return render_template("inventory/import_stock.html")

@bp.route("/stock/export")
def export_stock():
    stocks = Stock.query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "item_id", "location_id", "quantity"])
    for s in stocks:
        writer.writerow([s.id, s.item_id, s.location_id, s.quantity])
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=stock.csv"
    return response


############################
# TRANSACTION ROUTES
############################

@bp.route("/receiving")
def receiving():
    return render_template("inventory/receiving.html")

@bp.route("/issue")
def issue():
    return render_template("inventory/issue.html")

@bp.route("/move")
def move():
    return render_template("inventory/move.html")

@bp.route("/cycle-count")
def cycle_count():
    return render_template("inventory/cycle_count.html")

@bp.route("/history")
def history():
    return render_template("inventory/history.html")
