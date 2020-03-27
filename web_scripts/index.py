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

def simple_table(title, columns, rows, urls=None, urli=0, instructions=None, creation=None, action=None):
    if urls is None:
        urls = [None] * len(rows)
    rows = [[(url, cell) if ci == urli else (None, cell) for ci, cell in enumerate(row)] for url, row in zip(urls, rows)]
    if instructions is None:
        instructions = ""
    else:
        instructions = render(instructions)
    return {"template": "simpletable.html", "title": title, "columns": columns, "rows": rows, "urls": urls, "instructions": instructions, "creation": creation, "action": action}

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

def primary_shopping_trip():
    st = db.query(db.ShoppingTrip).filter_by(primary=True).all()
    return None if len(st) != 1 else st[0]

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
    trip = primary_shopping_trip()
    if trip is None:
        return {"template": "error.html", "message": "no shopping trip was marked as primary"}
    trip_date = str(trip.date)
    mycostid = cost_object_for_user(user)
    if mycostid is None:
        return {"template": "error.html", "message": "could not find cost object for user %s" % user}
    allowable_cost_ids = cost_objects_for_no_user() + [mycostid]

    items = item_names_by_uids()
    costs = cost_objects_by_uids()
    objects = db.query(db.Request).filter_by(tripid=trip.uid, contact=user).order_by(db.Request.submitted_at).all()
    rows = build_table(objects, lambda i: items.get(i.itemid, "#REF?"), "description", lambda i: render_quantity(i.quantity, i.unit), "substitution", lambda i: costs.get(i.costid, "#REF?"), "coop_date", "comments", "state")
    creation = [
        ("dropdown", "formal_name", [("", "")] + sorted(items.items(), key=lambda x: x[1])),
        ("text", "informal_name", ""),
        ("text", "quantity", "0 oz"),
        ("text", "substitutions", "No substitutions accepted."),
        ("dropdown", "cost_object", sorted([(costid, description) for (costid, description) in costs.items() if costid in allowable_cost_ids])),
        ("date", "coop_date", ""),
        ("text", "comments", ""),
        ("", "", "draft"),
    ]
    instructions = {
        "template": "request.html",
        "date": trip_date,
        "user": user,
    }
    return simple_table("Request Entry List for " + trip_date, ["Formal Item Name", "Informal Description", "Quantity", "Substitution Requirements", "Cost Object", "Co-op Date", "Comments", "State"], rows, instructions=instructions, creation=creation, action="?mode=debug")

@mode
def request_submit(user, write_access, params):
#    db.Request(

    return request_entry(user, write_access, {})

@mode
def debug(user, write_access, params):
    return simple_table("DEBUG DATA", ["Parameter Name", "Parameter Value"], sorted(params.items()))

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

def render(results):
    return jenv.get_template(results["template"]).render(**results)

def print_index():
    page = render(process_index()).encode("utf-8")

    print("Content-type: text/html\n")
    print(page)

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

if __name__ == "__main__":
    print_index()
