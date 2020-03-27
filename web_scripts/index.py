#!/usr/bin/python2
# -*- coding: utf-8 -*-
import cgitb; cgitb.enable()
import cgi
import db
import os
import jinja2
import kerbparse
import moira
import urlparse
from collections import namedtuple

QM = "cela"

jenv = jinja2.Environment(loader=jinja2.FileSystemLoader("templates"), autoescape=True)

def get_authlink():
    return ("https://" + os.environ["HTTP_HOST"].split(":")[0] + ":444" + os.environ["REQUEST_URI"])

modes = {}

def mode(f):
    modes[f.__name__] = f

@mode
def overview(user, write_access, params):
    return {"template": "index.html", "user": user, "write_access": write_access}

def build_table(objects, *columns):
    return [[(getattr(obj, col) if type(col) == str else col(obj)) for col in columns] for obj in objects]

def simple_table(title, columns, rows, urls=None, urli=0, instructions=None, creation=None):
    if urls is None:
        urls = [None] * len(rows)
    rows = [[(url, cell) if ci == urli else (None, cell) for ci, cell in enumerate(row)] for url, row in zip(urls, rows)]
    return {"template": "simpletable.html", "title": title, "columns": columns, "rows": rows, "urls": urls, "instructions": instructions, "creation": creation}

@mode
def cost(user, write_access, params):
    objects = db.query(db.CostObject).all()
    rows = build_table(objects, "description", "kerberos", "venmo")
    return simple_table("Cost Object List", ["Description", "Kerberos", "Venmo"], rows)

@mode
def locations(user, write_access, params):
    objects = db.query(db.Location).all()
    rows = build_table(objects, "name")
    return simple_table("Location List", ["Name"], rows)

@mode
def item_types(user, write_access, params):
    objects = db.query(db.ItemType).all()
    rows = build_table(objects, "name", "standard_unit")
    return simple_table("Item Type List", ["Name", "Standard Unit"], rows)

def item_names_by_uids():
    return {it.uid: it.name for it in db.query(db.ItemType).all()}

def locations_by_uids():
    return {loc.uid: loc.name for loc in db.query(db.Location).all()}

def primary_shopping_trip_id():
    st = db.query(db.ShoppingTrip).filter_by(primary=True).all()
    return None if len(st) != 1 else st[0].uid

def cost_objects_by_uids():
    return {co.uid: co.description for co in db.query(db.CostObject).all()}

def cost_objects_for_no_user():
    return [co.uid for co in db.query(db.CostObject).filter_by(kerberos=None).all()]

def cost_object_for_user(user):
    co = db.query(db.CostObject).filter_by(kerberos=user).all()
    return None if len(co) != 1 else co[0].uid

def render_quantity(quantity, unit):
    quantity = str(quantity)
    fq = float(quantity)
    if not fq:
        return "none"
    if quantity.endswith(".00"):
        fq = int(fq)
    return "%s %s" % (fq, unit)

@mode
def inventory(user, write_access, params):
    items = item_names_by_uids()
    locations = locations_by_uids()
    objects = db.query(db.Inventory).all()
    rows = build_table(objects, lambda i: items.get(i.itemid, "#REF?"), lambda i: render_quantity(i.quantity, i.unit), lambda i: locations.get(i.locationid, "#REF?"), "measurement")
    rows.sort(key=lambda row: (row[0], row[2]))
    # TODO: remove updated entries that end up looking like duplicates
    return simple_table("Inventory", ["Name", "Quantity", "Location", "Last Inventoried At"], rows)

@mode
def trips(user, write_access, params):
    objects = db.query(db.ShoppingTrip).all()
    rows = build_table(objects, "date")
    urls = ["?mode=requests&trip=%d" % i.uid for i in objects]
    return simple_table("Shopping Trip List", ["Date"], rows, urls)

@mode
def requests(user, write_access, params):
    if "trip" not in params or not params["trip"].isdigit():
        return {"template": "error.html", "message": "unrecognized trip ID"}
    tripid = int(params["trip"])

    items = item_names_by_uids()
    costs = cost_objects_by_uids()
    objects = db.query(db.Request).filter_by(tripid=int(params["trip"])).order_by(db.Request.submitted_at).all()
    rows = build_table(objects, lambda i: items.get(i.itemid, "#REF?"), "description", lambda i: render_quantity(i.quantity, i.unit), "substitution", "contact", lambda i: costs.get(i.costid, "#REF?"), "coop_date", "comments", "submitted_at", "state", "updated_at")
    return simple_table("Request Review List", ["Formal Item Name", "Informal Description", "Quantity", "Substitution Requirements", "Contact", "Cost Object", "Co-op Date", "Comments", "Submitted At", "State", "Updated At"], rows)

@mode
def request_entry(user, write_access, params):
    tripid = primary_shopping_trip_id()
    if tripid is None:
        return {"template": "error.html", "message": "no shopping trip was marked as primary"}
    mycostid = cost_object_for_user(user)
    if mycostid is None:
        return {"template": "error.html", "message": "could not find cost object for user %s" % user}
    allowable_cost_ids = cost_objects_for_no_user() + [mycostid]

    items = item_names_by_uids()
    costs = cost_objects_by_uids()
    objects = db.query(db.Request).filter_by(tripid=tripid, contact=user).order_by(db.Request.submitted_at).all()
    rows = build_table(objects, lambda i: items.get(i.itemid, "#REF?"), "description", lambda i: render_quantity(i.quantity, i.unit), "substitution", lambda i: costs.get(i.costid, "#REF?"), "coop_date", "comments", "submitted_at", "state", "updated_at")
    instructions = """
        Use this form to submit your items, one per row.
        Please limit the quantity of personal supplies you request, especially supplies that require fridge space.
        When possible, please request supplies as communal instead of personal.
        Personal supplies MUST be requested in quantities that can be purchased individually -- for example, do not request 1/2 cup of milk; if you need personal milk, and communal milk will not do, you must request a size of milk that is actually sold, such as 1 quart. If you request a smaller quantity than is available in an individual package, your request will be rounded up to the next size of package.
        To request an item personally, put your name in the "Cost Object" field. To request an item for co-op, say so in the cost object field, and fill out the co-op date field. To request an item for communal uses, say so in the cost object field.
        Be specific; I will get you any item that matches your description. For example: if you specify "milk", you might get skim milk, 1% milk, 2% milk, goat's milk, almond milk, et cetera. You might want to specify "3 cups of 1% cow's milk" instead, if you have specific needs.
        If an item is not available as requested, it may not be purchased; please specify what would be an appropriate substitute.
        Please submit co-op supply requests even if we already have the relevant supplies, so that the items can be set aside if we have them, or purchased if not. This DOES NOT apply to personal supplies. Communal supplies that we already have will be purchased at the QM's discretion.
        If you can, please take the time to find the item you want in the "Formal Item" list. If you can't find it, or if the formal item name doesn't accurately represent what you want, use the "Informal Description" box instead, and I'll assign a formal item name later.
        Quantities are most useful when specified in ounces, except for fluids, which are best specified in cups or fluid ounces.
        Once you're satisfied with your requests, please change them to the "SUBMITTED" state. If you decide that you don't actually want an item, please change it to the "RETRACTED" state. You can amend your requests at any point until they've been updated to the "ACCEPTED" state or anything beyond.
    """
    creation = [
        ("dropdown", "formal_name", sorted(items.items(), key=lambda x: x[1])),
        ("text", "informal_name", ""),
        ("text", "quantity", "0 oz"),
        ("text", "substitutions", "No substitutions accepted."),
        ("dropdown", "cost_object", sorted([(costid, description) for (costid, description) in costs.items() if costid in allowable_cost_ids], key=lambda x: x[1])),
        ("date", "coop_date", ""),
        ("text", "comments", ""),
        ("", "", "now"),
        ("", "", "DRAFT"),
        ("", "", "now"),
    ]
    return simple_table("Request Entry List", ["Formal Item Name", "Informal Description", "Quantity", "Substitution Requirements", "Cost Object", "Co-op Date", "Comments", "Submitted At", "State", "Updated At"], rows, instructions=instructions, creation=creation)

def process_index():
    user = kerbparse.get_kerberos()
    if not user:
        return {"template": "login.html", "authlink": get_authlink()}

    if not moira.has_access(user, "qazoo@mit.edu"):
        return {"template": "noaccess.html", "user": user}

    write_access = (user == QM)
    fields = cgi.FieldStorage()
    params = {field: fields[field].value for field in fields}

    mode = params.get("mode", "") or "overview"

    if mode not in modes:
        return {"template": "notfound.html"}

    return modes[mode](user, write_access, params)

def print_index():
    results = process_index()

    print("Content-type: text/html\n")
    print(jenv.get_template(results["template"]).render(**results).encode("utf-8"))

def print_rack(rack_name):
    user, authlink = get_auth()
    can_update = is_hwop(user)
    rack = db.get_rack(rack_name)
    print("Content-type: text/html\n")
    print(jenv.get_template("rack.html").render(rack=rack, user=user, authlink=authlink, can_update=can_update).encode("utf-8"))

Device = namedtuple("Device", "name rack rack_first_slot rack_last_slot ip contact owner service_level model notes")
DeviceDelta = namedtuple("DeviceDelta", "diff new old")

def to_device(device):
    return Device(name = device.name,
                  rack = device.rack,
                  rack_first_slot = device.rack_first_slot,
                  rack_last_slot = device.rack_last_slot,
                  ip = device.ip,
                  contact = device.contact,
                  owner = device.owner,
                  service_level = device.service_level,
                  model = device.model,
                  notes = device.notes)

def device_diff(old, new):
    old, new = to_device(old), to_device(new)
    return Device(*[(elem_old != elem_new) for elem_old, elem_new in zip(old, new)])

def compute_device_changes(history):
    deltas = []
    old = Device(name="", rack="", rack_first_slot=0, rack_last_slot=0, ip="", contact=None, owner=None, service_level="", model="", notes="")
    for new in history:
        deltas.append(DeviceDelta(diff=device_diff(old, new), new=new, old=old))
        old = new
    return deltas

def print_device(device_id):
    user, authlink = get_auth()
    device_history = db.get_device_history(device_id)

    if not device_history:
        print "Content-type: text/plain\n"
        print "no such device ID", device_id
        return

    latest_device = device_history[-1]
    latest_rack = db.get_rack(latest_device.rack)
    can_update = can_edit(user, latest_device)

    computed_history = compute_device_changes(device_history)
    computed_history.reverse()
    stella = moira.stella(latest_device.name)
    print "Content-type: text/html\n"
    print jenv.get_template("device.html").render(device=latest_device, rack=latest_rack, history=computed_history, user=user, authlink=authlink, can_update=can_update, stella=stella).encode("utf-8")

def print_add(rack_name, slot):
    user, authlink = get_auth()
    can_update = is_hwop(user)
    rack = db.get_rack(rack_name)
    email = moira.user_to_email(user)
    assert 1 <= slot <= rack.height
    print("Content-type: text/html\n")
    print(jenv.get_template("add.html").render(rack=rack, user=user, email=email, slot=slot, authlink=authlink, can_update=can_update).encode("utf-8"))

def print_parts():
    user, authlink = get_auth()
    can_update = is_hwop(user)
    parts = db.get_all_parts()
    latest = db.get_latest_inventory()
    all_skus = set(part.sku for part in parts)
    assert all(sku in all_skus for sku in latest)
    parts = sorted((part, latest.get(part.sku)) for part in parts)
    print("Content-type: text/html\n")
    print(jenv.get_template("parts.html").render(parts=parts, user=user, authlink=authlink, can_update=can_update).encode("utf-8"))

def print_part(sku):
    user, authlink = get_auth()
    can_update = is_hwop(user)
    part = db.get_part(sku)
    inventory = sorted(db.get_inventory(sku), key=lambda i: -i.txid)
    stock = inventory[0].new_count if inventory else 0
    inventory = [(step, step.new_count - previous) for step, previous in zip(inventory, [step.new_count for step in inventory[1:]] + [0])]
    print("Content-type: text/html\n")
    print(jenv.get_template("part.html").render(part=part, stock=stock, inventory=inventory, user=user, authlink=authlink, can_update=can_update).encode("utf-8"))

def print_add_part():
    user, authlink = get_auth()
    can_update = is_hwop(user)
    print("Content-type: text/html\n")
    print(jenv.get_template("add-part.html").render(user=user, authlink=authlink, can_update=can_update).encode("utf-8"))

# TODO: figure out better error handling for everything
def perform_add(params):
    user = kerbparse.get_kerberos()
    if not is_hwop(user):
        raise Exception("no access")
    rack = db.get_rack(params["rack"])
    first, last = int(params["first"]), int(params["last"])
    assert 1 <= first <= last <= rack.height
    if not moira.is_email_valid_for_owner(params["owner"]):
        raise Exception("bad owner")
    devid = db.DeviceIDs()
    db.add(devid)
    dev = db.DeviceUpdates(id=devid.id, name=params["devicename"], rack=params["rack"], rack_first_slot=first, rack_last_slot=last, ip=params.get("ip", ""), contact=params["contact"], owner=params["owner"], service_level=params.get("service", ""), model=params.get("model", ""), notes=params.get("notes", ""), last_updated_by=user)
    db.add(dev)
    print("Content-type: text/html\n")
    print(jenv.get_template("done.html").render(id=dev.id).encode("utf-8"))

def perform_update(params):
    user = kerbparse.get_kerberos()
    device = db.get_device_latest(params["id"])
    if not can_edit(user, device):
        raise Exception("no access")
    rack = db.get_rack(params["rack"])
    first, last = int(params["first"]), int(params["last"])
    assert 1 <= first <= last <= rack.height
    if not moira.is_email_valid_for_owner(params["owner"]):
        raise Exception("bad owner")
    ndevice = db.DeviceUpdates(
        id = device.id,
        name = params["devicename"],
        rack = params["rack"],
        rack_first_slot = first,
        rack_last_slot = last,
        ip = params.get("ip", ""),
        contact = params["contact"],
        owner = params["owner"],
        service_level = params.get("service", ""),
        model = params.get("model", ""),
        notes = params.get("notes", ""),
        last_updated_by = user,
    )
    db.add(ndevice)
    db.session.commit()
    print("Content-type: text/html\n")
    print(jenv.get_template("done.html").render(id=device.id).encode("utf-8"))

def perform_add_part(params):
    user = kerbparse.get_kerberos()
    if not is_hwop(user):
        raise Exception("no access")
    part = db.Parts(sku=params["sku"], description=params.get("description", ''), notes=params.get("notes", ""), last_updated_by=user)
    db.add(part)
    print("Content-type: text/html\n")
    print(jenv.get_template("done-part.html").render(sku=part.sku).encode("utf-8"))

def perform_update_stock(params):
    user = kerbparse.get_kerberos()
    if not is_hwop(user):
        raise Exception("no access")
    update = db.Inventory(sku=params["sku"], new_count=int(params["count"]), comment=params.get("comment", ""), submitted_by=user)
    db.add(update)
    print("Content-type: text/html\n")
    print(jenv.get_template("done-part.html").render(sku=update.sku).encode("utf-8"))

if __name__ == "__main__":
    print_index()
