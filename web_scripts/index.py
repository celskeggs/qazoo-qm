#!/usr/bin/python2
# -*- coding: utf-8 -*-
import cgitb; cgitb.enable()
import cgi
import datetime
import db
import os
import jinja2
import json
import kerbparse
import moira
import urlparse
from collections import namedtuple

QM = "cela"

jenv = jinja2.Environment(loader=jinja2.FileSystemLoader("templates"), autoescape=True, trim_blocks=True, lstrip_blocks=True)

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

def simple_table(title, columns, rows, urls=None, urli=0, instructions=""):
    if urls is None:
        urls = [None] * len(rows)
    rows = [[("url", url, "", cell) if ci == urli and url is not None else ("", "", "", cell) for ci, cell in enumerate(row)] for url, row in zip(urls, rows)]
    return {"template": "simpletable.html", "title": title, "columns": columns, "rows": rows, "instructions": instructions, "creation": None, "action": None, "optionsets": None}

def editable_table(title, columns, rows, instructions=None, creation=None, action=None, optionsets=None):
    if instructions is None:
        instructions = ""
    elif type(instructions) is dict:
        instructions = jinja2.Markup(render(instructions))
    return {"template": "simpletable.html", "title": title, "columns": columns, "rows": rows, "instructions": instructions, "creation": creation, "action": action, "optionsets": json.dumps(optionsets) if optionsets else None}

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

def get_shopping_trip(tripid):
    st = db.query(db.ShoppingTrip).filter_by(uid=tripid).all()
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
    if parsed != (fq, unit.strip()):
        raise ValueError("mismatch between render_quantity and parse_quantity: %s instead of %s" % (parsed, (fq, unit.strip())))
    return textual

def parse_quantity(quantity):
    if type(quantity) != str:
        return None, None
    parts = quantity.strip().split(" ", 1)
    if len(parts) == 1:
        parts += ["units"]
    quantity, unit = parts[0].strip(), parts[1].strip()
    try:
        quantity = float(quantity)
    except ValueError:
        return None, None
    if not unit.replace(" ","").isalpha():
        return None, None
    return quantity, unit

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
    objects = build_latest_inventory()
    rows = build_table(objects, lambda i: items.get(i.itemid, "#REF?"), lambda i: render_quantity(i.quantity, i.unit), lambda i: locations.get(i.locationid, "#REF?"), "measurement")
    rows.sort(key=lambda row: (row[0], row[2]))
    return simple_table("Inventory", ["Name", "Quantity", "Location", "Last Inventoried At"], rows, instructions="Number of inventory entries: %d" % len(rows))

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
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}
    edit = (param_as_str(params, "edit", "") == "true" and write_access)

    items = item_names_by_uids()
    costs = cost_objects_by_uids()

    formal_options = [("", "")] + sorted(items.items(), key=lambda x: x[1])
    cost_objects = sorted(costs.items())

    objects = db.query(db.Request).filter_by(tripid=tripid).order_by(db.Request.submitted_at).all()

    optionsets = {
        "formal_options": formal_options,
        "cost_objects": cost_objects,
    }

    if not edit:
        check = []
        rows = [
            [
                ("", "", "", i.uid                              ),
                ("", "", "", get_by_id(items, i.itemid)         ),
                ("", "", "", i.description or ""                ),
                ("", "", "", render_quantity(i.quantity, i.unit)),
                ("", "", "", i.substitution                     ),
                ("", "", "", i.contact                          ),
                ("", "", "", costs.get(i.costid, "#REF?")       ),
                ("", "", "", i.coop_date                        ),
                ("", "", "", i.comments                         ),
                ("", "", "", i.submitted_at                     ),
                ("", "", "", i.state                            ),
                ("", "", "", i.updated_at                       ),
            ] for i in objects
        ]
        action = None
    else: # if edit
        check = ["Edit?"]
        rows = [
            [
                ("checkbox",                  "edit.%d" % i.uid, "",                        False                              ),
                ("",                                         "", "",                        i.uid                              ),
                ("dropdown-optionset", "formal_name.%d" % i.uid, "formal_options",          i.itemid or ""                     ),
                ("text",             "informal_name.%d" % i.uid, "",                        i.description or ""                ),
                ("text",                  "quantity.%d" % i.uid, "",                        render_quantity(i.quantity, i.unit)),
                ("text",             "substitutions.%d" % i.uid, "",                        i.substitution                     ),
                ("",                                         "", "",                        i.contact                          ),
                ("dropdown-optionset", "cost_object.%d" % i.uid, "cost_objects",            i.costid                           ),
                ("date",                 "coop_date.%d" % i.uid, "",                        str(i.coop_date)                   ),
                ("text",                  "comments.%d" % i.uid, "",                        i.comments                         ),
                ("",                                         "", "",                        str(i.submitted_at)                ),
                ("dropdown",                 "state.%d" % i.uid, state_options(i, qm=True), i.state                            ),
                ("",                                         "", "",                        str(i.updated_at)                  ),
            ] for i in objects
        ]
        action = "?mode=request_modify&trip=%d" % trip.uid
    instructions = {
        "template": "reviewlist.html",
        "can_edit": write_access,
        "edit": edit,
        "editlink": "?mode=requests&trip=%d&edit=%s" % (trip.uid, str(not edit).lower()),
        "inventorylink": "?mode=inventory_review_list&trip=%d" % (trip.uid),
        "reservelink": "?mode=reservation_preparation&trip=%d" % (trip.uid),
        "count": len(objects),
    }
    return editable_table("Request Review List for " + str(trip.date), check + ["ID", "Formal Item Name", "Informal Description", "Quantity", "Substitution Requirements", "Contact", "Cost Object", "Co-op Date", "Comments", "Submitted At", "State", "Updated At"], rows, instructions=instructions, action=action, optionsets=optionsets)

def allowable_states(request, qm=False):
    return [request.state] + db.RequestState.ALLOWABLE[request.state][qm]

def state_options(request, qm=False):
    return [(state, state) for state in allowable_states(request, qm=qm)]

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

    default_costid = (objects[-1].costid if objects else "")
    default_date = (objects[-1].coop_date if objects else "")
    default_substitutions = "No substitutions accepted."

    optionsets = {
        "formal_options": formal_options,
        "cost_objects": cost_objects,
    }

    rows = [
        [
            ("checkbox",                  "edit.%d" % i.uid, "",               False                              ),
            ("dropdown-optionset", "formal_name.%d" % i.uid, "formal_options", i.itemid or ""                     ),
            ("text",             "informal_name.%d" % i.uid, "",               i.description or ""                ),
            ("text",                  "quantity.%d" % i.uid, "",               render_quantity(i.quantity, i.unit)),
            ("text",             "substitutions.%d" % i.uid, "",               i.substitution                     ),
            ("dropdown-optionset", "cost_object.%d" % i.uid, "cost_objects",   i.costid                           ),
            ("date",                 "coop_date.%d" % i.uid, "",               str(i.coop_date)                   ),
            ("text",                  "comments.%d" % i.uid, "",               i.comments                         ),
        # note: state_options is called with QM=false because while QMs do have special powers, they should not be used from this part of the interface
            ("dropdown",                 "state.%d" % i.uid, state_options(i), i.state                            ),
        ] for i in objects
    ]
    creation = [
        ("",                                  "", "",               ""                   ),
        ("dropdown-optionset", "formal_name.new", "formal_options", ""                   ),
        ("text",             "informal_name.new", "",               ""                   ),
        ("text",                  "quantity.new", "",               "0 oz"               ),
        ("text",             "substitutions.new", "",               default_substitutions),
        ("dropdown-optionset", "cost_object.new", "cost_objects",   default_costid       ),
        ("date",                 "coop_date.new", "",               default_date         ),
        ("text",                  "comments.new", "",               ""                   ),
        ("",                                  "", "",               "draft"              ),
    ]
    instructions = {
        "template": "request.html",
        "date": trip_date,
        "user": user,
    }
    return editable_table("Request Entry Form for " + trip_date, ["Edit?", "Formal Item Name", "Informal Description", "Quantity", "Substitution Requirements", "Cost Object", "Co-op Date", "Comments", "State"], rows, instructions=instructions, creation=creation, action="?mode=request_submit&trip=%d" % trip.uid, optionsets=optionsets)

def create_request_from_params(params, suffix, tripid, contact, allowable_cost_ids, allowable_states):
    formal_name = int_or_none(params, "formal_name" + suffix)
    informal_name = param_as_str(params, "informal_name" + suffix, None)
    if type(informal_name) == list:
        informal_name = None
    if formal_name == None and informal_name == None:
        return None

    costid = int_or_none(params, "cost_object" + suffix)
    if not costid:
        return "no cost ID specified"

    if costid not in allowable_cost_ids:
        return "attempt to submit under invalid cost ID"

    quantity, unit = parse_quantity(param_as_str(params, "quantity" + suffix, ""))
    if quantity is None:
        return "quantity not provided in required <NUMBER> <UNIT> format"

    state = param_as_str(params, "state" + suffix, db.RequestState.draft)
    if state not in allowable_states:
        return "invalid state: %s" % repr(state)

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
        state = state,
    )

def merge_changes(target, source):
    changes = False

    for field in ["itemid", "costid", "description", "unit", "substitution", "comments", "state"]:
        if getattr(target, field) != getattr(source, field):
            setattr(target, field, getattr(source, field))
            changes = True

    if str(target.coop_date) != str(source.coop_date):
        target.coop_date = source.coop_date
        changes = True

    # since smallest step value of our Decimal is 0.01
    if abs(float(target.quantity) - float(source.quantity)) >= 0.005:
        target.quantity = source.quantity
        changes = True

    # TODO: validate state changes

    if changes:
        target.updated_at = source.updated_at

    return changes

def handle_request_updates(user, write_access, params, trip, require_edit=False):
    if write_access:
        allowable_cost_ids = cost_objects_by_uids().keys()
    else:
        allowable_cost_ids = allowable_cost_object_uids(user)
        if allowable_cost_ids is None:
            return {"template": "error.html", "message": "could not find cost object for user %s" % user}

    uids = {int(param[6:]) for param in params if param.startswith("state.") and param[6:].isdigit()}
    if write_access:
        requests = db.query(db.Request).filter_by(tripid=trip.uid).all()
    else:
        requests = db.query(db.Request).filter_by(tripid=trip.uid, contact=user).all()
    available = set(request.uid for request in requests)
    for uid in uids:
        if uid not in available:
            return {"template": "error.html", "message": "request %d did not exist in an updateable form" % uid}

    any_edits = False
    for request in requests:
        if request.uid not in uids:
            continue

        if require_edit and params.get("edit.%d" % request.uid) != "on":
            continue

        updated_request = create_request_from_params(params, ".%d" % request.uid, tripid=trip.uid, contact=user, allowable_cost_ids=allowable_cost_ids, allowable_states=allowable_states(request, qm=write_access))
        if updated_request is None:
            return {"template": "error.html", "message": "attempt to change request to have no item name, formal or informal"}
        if type(updated_request) == str:
            return {"template": "error.html", "message": updated_request}
        if merge_changes(request, updated_request):
            any_edits = True

    new_request = create_request_from_params(params, ".new", tripid=trip.uid, contact=user, allowable_cost_ids=allowable_cost_ids, allowable_states=[db.RequestState.draft])
    if type(new_request) == str:
        return {"template": "error.html", "message": new_request}
    if new_request is not None:
        db.add(new_request)
    elif any_edits:
        # needed to make sure any edits from before actually get applied; db.add does this automatically
        db.commit()
    return None

@mode
def request_submit(user, write_access, params):
    trip = primary_shopping_trip()
    if trip is None or params.get("trip","") != str(trip.uid):
        return {"template": "error.html", "message": "primary shopping trip changed between page load and form submit"}

    res = handle_request_updates(user, False, params, trip, require_edit=True)
    if res is not None:
        return res
    return request_entry(user, write_access, {})

@mode
def request_modify(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}
    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}
    res = handle_request_updates(user, write_access, params, trip, require_edit=True)
    if res is not None:
        return res
    return requests(user, write_access, {"trip": params["trip"], "edit": "true"})

def build_latest_inventory(*filter_queries):
    inventory = db.query(db.Inventory).filter(*filter_queries).order_by(db.Inventory.measurement).all()

    # only take the latest measurement by location and item type
    inventory_latest = {}
    for i in inventory:
        key = (i.itemid, i.locationid)
        if key in inventory_latest:
            assert inventory_latest[key].measurement <= i.measurement
        inventory_latest[key] = i
    return list(inventory_latest.values())

@mode
def inventory_review_list(user, write_access, params):
    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}

    valid_costids = [co.uid for co in db.query(db.CostObject).filter_by(kerberos=None).all()]
    requests = db.query(db.Request).filter(db.Request.itemid != None, db.Request.tripid == tripid, ~db.Request.state.in_([db.RequestState.retracted, db.RequestState.rejected]), db.Request.costid.in_(valid_costids)).all()
    relevant_itemids = {r.itemid for r in requests}
    inventory = build_latest_inventory(db.Inventory.itemid.in_(relevant_itemids))

    request_ids = {itemid: [] for itemid in relevant_itemids}
    for request in requests:
        request_ids[request.itemid].append(request.uid)

    locations = locations_by_uids()
    items = item_names_by_uids()

    yesterday = datetime.date.fromordinal(datetime.date.today().toordinal() - 1)
    rows = [(
        ("", "", "", "yes" if i.measurement >= yesterday else "no"),
        ("", "", "", locations[i.locationid]),
        ("", "", "", items[i.itemid]),
        ("", "", "", render_quantity(i.quantity, i.unit)),
        ("text", "quantity.%d.%d" % (i.itemid, i.locationid), "", ""),
        ("", "", "", str(i.measurement)),
        ("", "", "", ", ".join(map(str,request_ids[i.itemid]))),
    ) for i in inventory]
    rows.sort()

    count = sum(i.measurement < yesterday for i in inventory)

    instructions = "Found %d items for inventory" % count

    return editable_table("Inventory Incremental Review", ["Up-to-date?", "Location", "Item", "Inventory Quantity", "New Quantity", "Last Inventoried", "Request IDs"], rows, action=("?mode=inventory_update&trip=%d" % trip.uid if write_access else None), instructions=instructions)

@mode
def inventory_update(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}
    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}

    locations = locations_by_uids()
    items = item_names_by_uids()

    today = datetime.date.today()

    any_updates = False
    for p in params:
        if p.startswith("quantity.") and p.count(".") == 2:
            _, itemid, locationid = p.split(".")
            try:
                itemid, locationid = int(itemid), int(locationid)
            except ValueError:
                return {"template": "error.html", "message": "invalid item or location ID"}
            if itemid not in items:
                return {"template": "error.html", "message": "unrecognized item ID %d" % itemid}
            if locationid not in locations:
                return {"template": "error.html", "message": "unrecognized location ID %d" % locationid}
            quantity, unit = parse_quantity(params[p])
            if quantity is None:
                return {"template": "error.html", "message": "invalid quantity %s" % params[p]}

            update = db.Inventory(
                itemid=itemid,
                quantity=quantity,
                unit=unit,
                locationid=locationid,
                measurement=today,
            )
            db.add_no_commit(update)
            any_updates = True
    if any_updates:
        db.commit()

    return inventory_review_list(user, write_access, params)

@mode
def reservations(user, write_access, params):
    today = datetime.date.today()
    reservations = db.query(db.Reservation).filter(db.Reservation.until >= today).all()
    loc_names = locations_by_uids()
    item_names = item_names_by_uids()

    by_location = {}
    for r in reservations:
        loc = loc_names.get(r.locationid, "#REF?")
        if loc not in by_location:
            by_location[loc] = []
        by_location[loc].append((item_names.get(r.itemid, "#REF?"), render_quantity(r.quantity, r.unit), r.until))

    for table in by_location.values():
        table.sort()

    locations = by_location.items()
    locations.sort()

    return {
        "template": "reservations.html",
        "locations": locations,
    }

@mode
def reservation_preparation(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}

    requests = db.query(db.Request).filter(db.Request.itemid != None, db.Request.tripid == tripid, db.Request.state.in_([db.RequestState.accepted, db.RequestState.to_purchase, db.RequestState.to_reserve]), db.Request.coop_date != None).all()

    inventory = db.query(db.Inventory).all()
    possible_locations = {}
    for i in inventory:
        if i.itemid not in possible_locations:
            possible_locations[i.itemid] = set()
        possible_locations[i.itemid].add(i.locationid)

    locations = locations_by_uids()
    items = item_names_by_uids()

    likely_locations = {itemid: list(s)[0] for itemid, s in possible_locations.items() if len(s) == 1 and list(s)[0] in locations}

    rows = [(
        ("", "", "", items[i.itemid]),
        ("dropdown", "location.%d" % i.itemid, [("", "")] + sorted(locations.items()), likely_locations.get(i.itemid,"")),
        ("", "", "", render_quantity(i.quantity, i.unit)),
        ("", "", "", i.coop_date),
    ) for i in requests]
    rows.sort()

    instructions = "Found %d reservations for submission; possible_locations = %s; likely_locations = %s; itemids = %s" % (len(requests), repr(possible_locations), repr(likely_locations), repr([i.itemid for i in requests]))

    return editable_table("Inventory Incremental Review", ["Item", "Location", "Quantity", "Date"], rows, action="?mode=reservations_submit&trip=%d" % trip.uid, instructions=instructions)

@mode
def reservations_submit(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}

    requests = db.query(db.Request).filter(db.Request.itemid != None, db.Request.tripid == tripid, db.Request.state.in_([db.RequestState.accepted, db.RequestState.to_purchase, db.RequestState.to_reserve]), db.Request.coop_date != None).all()

    count = 0
    for r in requests:
        locid = params.get("location.%d" % r.itemid)
        if not locid:
            continue
        db.add_no_commit(db.Reservation(
            until = r.coop_date,
            itemid = r.itemid,
            locationid = locid,
            quantity = r.quantity,
            unit = r.unit,
        ))
        count += 1
    if count:
        db.commit()

    return {
        "template": "submitted.html",
        "instructions": "Submitted %d reservations (%d skipped)" % (count, len(requests) - count),
        "target": "?mode=reservations",
    }

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

    if param_as_str(params, "act") == "mortal":
        write_access = False

    return modes[mode](user, write_access, params)

def render(results):
    return jenv.get_template(results["template"]).render(**results)

def print_index():
    page = render(process_index()).encode("utf-8")

    print("Content-type: text/html\n")
    print(page)

if __name__ == "__main__":
    print_index()
