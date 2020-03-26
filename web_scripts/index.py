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

def simple_table(title, columns, rows, urls=None, urli=0):
    if urls is None:
        urls = [None] * len(rows)
    rows = [[(url, cell) if ci == urli else (None, cell) for ci, cell in enumerate(row)] for url, row in zip(urls, rows)]
    return {"template": "simpletable.html", "title": title, "columns": columns, "rows": rows, "urls": urls}

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

def cost_objects_by_uids():
    return {co.uid: co.description for co in db.query(db.CostObject).all()}

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
    urls = ["?view=requests&trip=%d" % i.uid for i in objects]
    return simple_table("Shopping Trip List", ["Date"], rows, urls)

@mode
def requests(user, write_access, params):
    if "trip" not in params or not params["trip"].isdigit():
        return {"template": "notfound.html"}
    items = item_names_by_uids()
    costs = cost_objects_by_uids()
    objects = db.query(db.Request).filter_by(tripid=int(params["trip"])).filter_by(db.Request.submitted_at).all()
    rows = build_table(objects, lambda i: items.get(i.itemid, "#REF?"), "description", lambda i: render_quantity(i.quantity, i.unit), "substitution", "contact", lambda i: costs.get(i.costid, "#REF?"), "coop_date", "comments", "submitted_at", "updated_at")
    return simple_table("Item Type List", ["Formal Item Name", "Informal Description", "Quantity", "Substitution Requirements", "Contact", "Cost Object", "Co-op Date", "Comments", "Submitted At", "Updated At"], rows)

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
