#!/usr/bin/python2
# -*- coding: utf-8 -*-
import cgitb; cgitb.enable()
import cgi
import datetime
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
    return f

@mode
def overview(user, write_access, params):
    return {"template": "index.html", "user": user, "write_access": write_access}

def build_table(objects, *columns):
    return [[(getattr(obj, col) if type(col) == str else col(obj)) for col in columns] for obj in objects]

def simple_table(title, columns, rows, urls=None, urli=0):
    if urls is None:
        urls = [None] * len(rows)
    rows = [[("url", url, "", cell) if ci == urli and url is not None else ("", "", "", cell) for ci, cell in enumerate(row)] for url, row in zip(urls, rows)]
    return {"template": "simpletable.html", "title": title, "columns": columns, "rows": rows, "instructions": "", "creation": None, "action": None}

def editable_table(title, columns, rows, instructions=None, creation=None, action=None):
    if instructions is None:
        instructions = ""
    else:
        instructions = render(instructions)
    return {"template": "simpletable.html", "title": title, "columns": columns, "rows": rows, "instructions": instructions, "creation": creation, "action": action}

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

def allowable_cost_object_uids(user):
    # TODO: make this simpler
    uids = [co.uid for co in db.query(db.CostObject).filter_by(kerberos=None).all()]
    user_uids = [co.uid for co in db.query(db.CostObject).filter_by(kerberos=user).all()]
    if not user_uids:
        return None
    return uids + user_uids

def render_quantity(quantity, unit):
    fq = float(quantity)
    if str(quantity).endswith(".00"):
        fq = int(fq)
    textual = "%s %s" % (fq, unit)
    parsed = parse_quantity(textual)
    if parsed != (fq, unit):
        raise ValueError("mismatch between render_quantity and parse_quantity: %s instead of %s" % (parsed, (quantity, unit)))
    return textual

def parse_quantity(quantity):
    if type(quantity) != str:
        return None
    parts = quantity.strip().split(" ", 1)
    if len(parts) == 1:
        parts += ["units"]
    quantity, unit = parts[0].strip(), parts[1].strip()
    try:
        quantity = float(quantity)
    except ValueError:
        return None
    if not unit.replace(" ","").isalpha():
        return None
    return quantity, unit.lower()

def get_by_id(items, uid):
    if uid is None:
        return None
    return items.get(uid, "#REF?")

def param_as_str(params, name, default=None):
    l = params.get(name, [])
    if type(l) == list or not l:
        return default
    return l

def int_or_none(params, name):
    text = param_as_str(params, name, "")
    if text.isdigit():
        return int(text)
    else:
        return None

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
    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}

    items = item_names_by_uids()
    costs = cost_objects_by_uids()
    objects = db.query(db.Request).filter_by(tripid=tripid).order_by(db.Request.submitted_at).all()
    rows = build_table(objects, lambda i: get_by_id(items, i.itemid), "description", lambda i: render_quantity(i.quantity, i.unit), "substitution", "contact", lambda i: costs.get(i.costid, "#REF?"), "coop_date", "comments", "submitted_at", "state", "updated_at")
    return simple_table("Request Review List", ["Formal Item Name", "Informal Description", "Quantity", "Substitution Requirements", "Contact", "Cost Object", "Co-op Date", "Comments", "Submitted At", "State", "Updated At"], rows)

@mode
def request_entry(user, write_access, params):
    trip = primary_shopping_trip()
    if trip is None:
        return {"template": "error.html", "message": "no shopping trip was marked as primary"}
    trip_date = str(trip.date)
    allowable_cost_ids = allowable_cost_object_uids(user)
    if allowable_cost_ids is None:
        return {"template": "error.html", "message": "could not find cost object for user %s" % user}

    items = item_names_by_uids()
    costs = cost_objects_by_uids()
    objects = db.query(db.Request).filter_by(tripid=trip.uid, contact=user).order_by(db.Request.submitted_at).all()
    formal_options = [("", "")] + sorted(items.items(), key=lambda x: x[1])
    cost_objects = sorted([(costid, description) for (costid, description) in costs.items() if costid in allowable_cost_ids])
    # TODO: restrict these options based on allowable state transitions
    state_options = [(state, state) for state in db.RequestState.VALUES]
    rows = [
        [
            ("dropdown", "formal_name.%d" % i.uid, formal_options, i.itemid or ""                     ),
            ("text",   "informal_name.%d" % i.uid, "",             i.description or ""                ),
            ("text",        "quantity.%d" % i.uid, "",             render_quantity(i.quantity, i.unit)),
            ("text",   "substitutions.%d" % i.uid, "",             i.substitution                     ),
            ("dropdown", "cost_object.%d" % i.uid, cost_objects,   i.costid                           ),
            ("date",       "coop_date.%d" % i.uid, "",             str(i.coop_date)                   ),
            ("text",        "comments.%d" % i.uid, "",             i.comments                         ),
            ("dropdown",       "state.%d" % i.uid, state_options,  i.state                            ),
        ] for i in objects
    ]
    creation = [
        ("dropdown", "formal_name.new", formal_options              ),
        ("text",   "informal_name.new", ""                          ),
        ("text",        "quantity.new", "0 oz"                      ),
        ("text",   "substitutions.new", "No substitutions accepted."),
        ("dropdown", "cost_object.new", cost_objects                ),
        ("date",       "coop_date.new", ""                          ),
        ("text",        "comments.new", ""                          ),
        ("",                        "", "draft"                     ),
    ]
    instructions = {
        "template": "request.html",
        "date": trip_date,
        "user": user,
    }
    return editable_table("Request Entry Form for " + trip_date, ["Formal Item Name", "Informal Description", "Quantity", "Substitution Requirements", "Cost Object", "Co-op Date", "Comments", "State"], rows, instructions=instructions, creation=creation, action="?mode=request_submit&trip=%d" % trip.uid)

def create_request_from_params(params, suffix, tripid, contact, allowable_cost_ids):
    costid = int_or_none(params, "cost_object" + suffix)
    if not costid:
        return "no cost ID specified"

    if costid not in allowable_cost_ids:
        return "attempt to submit under invalid cost ID"

    formal_name = int_or_none(params, "formal_name" + suffix)
    informal_name = param_as_str(params, "informal_name" + suffix, None)
    if type(informal_name) == list:
        informal_name = None
    if formal_name == None and informal_name == None:
        return None

    quantity, unit = parse_quantity(param_as_str(params, "quantity" + suffix, ""))
    if quantity is None:
        return "quantity not provided in required <NUMBER> <UNIT> format"

    now = datetime.datetime.now()

    return db.Request(
        tripid = tripid,
        itemid = formal_name,
        costid = costid,
        description = informal_name,
        quantity = quantity,
        unit = unit,
        substitution = param_as_str(params, "substitutions" + suffix, "[no entry]"),
        contact = contact,
        coop_date = param_as_str(params, "coop_date" + suffix, None),
        comments = param_as_str(params, "comments" + suffix, ""),
        submitted_at = now,
        updated_at = now,
        state = db.RequestState.draft,
    )

def merge_changes(target, source):
    changes = []

    for field in ["itemid", "costid", "description", "unit", "substitution", "coop_date", "comments", "state"]:
        if getattr(target, field) != getattr(source, field):
            changes.append((field, repr(getattr(target, field)), repr(getattr(source, field))))
            setattr(target, field, getattr(source, field))

    # since smallest step value of our Decimal is 0.01
    if abs(float(target.quantity) - float(source.quantity)) >= 0.005:
        changes.append((field, target.quantity, source.quantity))
        target.quantity = source.quantity

    # TODO: validate state changes

    if changes:
        target.updated_at = source.updated_at

    return changes

@mode
def request_submit(user, write_access, params):
    trip = primary_shopping_trip()
    if trip is None or params.get("trip","") != str(trip.uid):
        return {"template": "error.html", "message": "primary shopping trip changed between page load and form submit"}

    allowable_cost_ids = allowable_cost_object_uids(user)
    if allowable_cost_ids is None:
        return {"template": "error.html", "message": "could not find cost object for user %s" % user}

    uids = {int(param[6:]) for param in params if param.startswith("state.") and param[6:].isdigit()}
    requests = db.query(db.Request).filter_by(tripid=trip.uid, contact=user).all()
    available = set(request.uid for request in requests)
    for uid in uids:
        if uid not in available:
            return {"template": "error.html", "message": "request %d did not exist in an updateable form" % uid}

    any_edits = False
    for request in requests:
        if request.uid not in uids:
            continue

        updated_request = create_request_from_params(params, ".%d" % request.uid, tripid=trip.uid, contact=user, allowable_cost_ids=allowable_cost_ids)
        if updated_request is None:
            return {"template": "error.html", "message": "attempt to change request to have no item name, formal or informal"}
        if type(updated_request) == str:
            return {"template": "error.html", "message": updated_request}
        changes = merge_changes(request, updated_request)
        if changes:
            any_edits = True

    new_request = create_request_from_params(params, ".new", tripid=trip.uid, contact=user, allowable_cost_ids=allowable_cost_ids)
    if type(new_request) == str:
        return {"template": "error.html", "message": new_request}
    if new_request is not None:
        db.add(new_request)
    elif any_edits:
        # needed to make sure any edits from before actually get applied; db.add does this automatically
        db.commit()

    return request_entry(user, write_access, {})

@mode
def debug(user, write_access, params):
    return simple_table("DEBUG DATA", ["Parameter Name", "Parameter Value"], sorted([(k, sorted(v) if type(v) == list else v) for k, v in params.items()]))

def process_index():
    user = kerbparse.get_kerberos()
    if not user:
        return {"template": "login.html", "authlink": get_authlink()}

    if not moira.has_access(user, "qazoo@mit.edu"):
        return {"template": "noaccess.html", "user": user}

    write_access = (user == QM)
    fields = cgi.FieldStorage()
    params = {field: ([f.value for f in fields[field]] if type(fields[field]) == list else fields[field].value) for field in fields}

    mode = param_as_str(params, "mode", "overview")

    if mode not in modes:
        return {"template": "notfound.html"}

    return modes[mode](user, write_access, params)

def render(results):
    return jenv.get_template(results["template"]).render(**results)

def print_index():
    page = render(process_index()).encode("utf-8")

    print("Content-type: text/html\n")
    print(page)

if __name__ == "__main__":
    print_index()
