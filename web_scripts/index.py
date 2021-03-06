#!/usr/bin/python3
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
from collections import namedtuple

QM = "cela"
QM_VENMO = "@celskeggs"

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

def simple_table(title, columns, rows, urls=None, urli=0, instructions=None):
    if urls is None:
        urls = [None] * len(rows)
    rows = [[("url", url, "", cell) if ci == urli and url is not None else ("", "", "", cell) for ci, cell in enumerate(row)] for url, row in zip(urls, rows)]
    return editable_table(title, columns, rows, instructions=instructions)

def editable_table(title, columns, rows, instructions=None, creation=None, action=None, optionsets=None, onedit=False, wrap=None, addspans=True, autoscroll=False):
    if instructions is None:
        instructions = ""
    elif type(instructions) is dict:
        instructions = jinja2.Markup(render(instructions))
    if addspans:
        if addspans is True:
            addspans = {}
        rows = [[(addspans.get(i), ctype, cname, options, cell) for i, (ctype, cname, options, cell) in enumerate(row)] for row in rows]
        columns = [(addspans.get(i), col) for i, col in enumerate(columns)]
    if wrap is None:
        headers = [columns]
        wrapped_rows = rows
    else:
        assert type(wrap) is int and wrap > 0 and wrap < len(columns)
        headers = [columns[:wrap], columns[wrap:]]
        wrapped_rows = []
        for row in rows:
            wrapped_rows.append(row[:wrap])
            wrapped_rows.append(row[wrap:])
    return {"template": "simpletable.html", "title": title, "headers": headers, "rows": wrapped_rows, "instructions": instructions, "creation": creation, "action": action, "optionsets": json.dumps(optionsets) if optionsets else None, "onedit": onedit, "autoscroll": autoscroll}

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
    rows = build_table(objects, "name", "standard_unit", "aisle")
    return simple_table("Item Type List", ["Name", "Standard Unit", "Aisle"], rows)

@mode
def item_types_edit(user, write_access, params):
    tripid = int_or_none(params, "trip")
    if tripid is not None:
        trip = get_shopping_trip(tripid)
        if trip is None:
            return {"template": "error.html", "message": "unrecognized trip ID"}

        requests = db.query(db.Request).filter(db.Request.tripid == tripid, ~db.Request.state.in_([db.RequestState.rejected, db.RequestState.retracted, db.RequestState.to_reserve])).all()
        items = {r.itemid for r in requests}

    objects = db.query(db.ItemType).all()
    rows = [
        [
            ("checkbox", "edit.%d" % i.uid, "", False),
            ("",                        "", "", i.uid),
            ("",                        "", "", i.name),
            ("",                        "", "", i.standard_unit),
            ("text",    "aisle.%d" % i.uid, "", i.aisle or ""),
        ] for i in objects if (tripid is None or i.uid in items)
    ]
    rows.sort(key=lambda r: r[1])
    creation = [
        ("",                "", "", ""),
        ("",                "", "", ""),
        ("text",    "name.new", "", ""),
        ("text",    "unit.new", "", ""),
        ("text",   "aisle.new", "", ""),
    ]

    return editable_table("Edit Item Types", ["Edit?", "ID", "Name", "Standard Unit", "Aisle"], rows, instructions=("All item types" if tripid is None else "Item types for trip on %s" % trip.date), action=("?mode=item_types_update" + ("&trip=%d" % tripid if tripid is not None else "") if write_access else None), onedit=True, creation=(creation if write_access else None))

@mode
def item_types_update(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    types = db.query(db.ItemType).all()
    lookup = {t.uid: t for t in types}

    edited = {int(p[5:]) for p in params if p.startswith("edit.") and p[5:].isdigit() and params[p] == "on"}

    count = 0
    for uid in edited:
        if uid not in lookup:
            return {"template": "error.html", "message": "no such item type with UID %d" % uid}
        t = lookup[uid]
        t.aisle = params.get("aisle.%d" % uid)
        count += 1
    if params.get("name.new"):
        new_itemtype = db.ItemType(
            name = params["name.new"].strip(),
            standard_unit = params.get("unit.new","").strip() or None,
            aisle = params.get("aisle.new","").strip() or None,
        )
        db.add(new_itemtype)
    elif count:
        db.commit()
    return item_types_edit(user, write_access, params)

def item_names_by_uids():
    return {it.uid: it.name for it in db.query(db.ItemType).all()}

def item_aisles_by_uids():
    return {it.uid: it.aisle for it in db.query(db.ItemType).all()}

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
    rows = build_table(objects, lambda i: i.item.name, lambda i: render_quantity(i.quantity, i.unit), lambda i: locations.get(i.locationid, "#REF?"), "measurement", lambda i: "yes" if i.full_inventory else "no")
    rows.sort(key=lambda row: (row[0], row[2]))
    return simple_table("Inventory", ["Name", "Quantity", "Location", "Last Updated At", "Full re-inventory?"], rows, instructions="Number of inventory entries: %d" % len(rows))

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
    locations = {l.uid: l.name for l in db.query(db.Location).all()}
    location_options = [("", "")] + sorted(locations.items())

    objects = db.query(db.Request).filter_by(tripid=tripid).order_by(db.Request.submitted_at).all()
    if "state" in params:
        expected_state = params["state"]
        if expected_state not in db.RequestState.VALUES:
            return {"template": "error.html", "message": "unrecognized state %s" % expected_state}
        objects = [o for o in objects if o.state == expected_state]

    optionsets = {
        "formal_options": formal_options,
        "cost_objects": cost_objects,
        "locations": location_options,
    }

    if not edit:
        columns = ["ID", "Formal Item Name", "Informal Description", "Quantity", "Contact", "Cost Object", "Co-op Date", "Submitted At", "State", "Updated At", "", "Substitution Requirements", "Comments", "Procurement Comments", "Procurement Location"]
        wrap = 10
        spans = {12: 2, 13: 4, 14: 2}
        rows = [
            [
                ("", "", "", i.uid                                       ),
                ("", "", "", get_by_id(items, i.itemid)                  ),
                ("", "", "", i.description or ""                         ),
                ("", "", "", render_quantity(i.quantity, i.unit)         ),
                ("", "", "", i.contact                                   ),
                ("", "", "", costs.get(i.costid, "#REF?")                ),
                ("", "", "", i.coop_date                                 ),
                ("", "", "", i.submitted_at                              ),
                ("", "", "", i.state                                     ),
                ("", "", "", i.updated_at                                ),
                # wrap
                ("", "", "", ""                                          ),
                ("", "", "", i.substitution                              ),
                ("", "", "", i.comments                                  ),
                ("", "", "", i.procurement_comments                      ),
                ("", "", "", get_by_id(locations, i.procurement_location)),
            ] for i in objects
        ]
        action = None
    else: # if edit
        columns = ["Edit?", "ID", "Formal Item Name", "Informal Description", "Quantity", "Contact", "Cost Object", "Co-op Date", "Submitted At", "State", "Updated At", "", "", "Substitution Requirements", "Comments", "Procurement Comments", "Procurement Location"]
        wrap = 11
        spans = {13: 2, 14: 4, 15: 2}
        rows = [
            [
                ("checkbox",                           "edit.%d" % i.uid, "",                        False                              ),
                ("",                                                  "", "",                        i.uid                              ),
                ("dropdown-optionset",          "formal_name.%d" % i.uid, "formal_options",          i.itemid or ""                     ),
                ("text",                      "informal_name.%d" % i.uid, "",                        i.description or ""                ),
                ("text",                           "quantity.%d" % i.uid, "",                        render_quantity(i.quantity, i.unit)),
                ("",                                                  "", "",                        i.contact                          ),
                ("dropdown-optionset",          "cost_object.%d" % i.uid, "cost_objects",            i.costid                           ),
                ("date",                          "coop_date.%d" % i.uid, "",                        str(i.coop_date)                   ),
                ("",                                                  "", "",                        str(i.submitted_at)                ),
                ("dropdown",                          "state.%d" % i.uid, state_options(i, qm=True), i.state                            ),
                ("",                                                  "", "",                        str(i.updated_at)                  ),
                # wrap
                ("",                                                  "", "",                        ""                                 ),
                ("",                                                  "", "",                        ""                                 ),
                ("text",                      "substitutions.%d" % i.uid, "",                        i.substitution                     ),
                ("text",                           "comments.%d" % i.uid, "",                        i.comments                         ),
                ("text",               "procurement_comments.%d" % i.uid, "",                        i.procurement_comments             ),
                ("dropdown-optionset", "procurement_location.%d" % i.uid, "locations",               i.procurement_location             ),
            ] for i in objects
        ]
        action = "?mode=request_modify&trip=%d" % trip.uid
        if "state" in params:
            action += "&state_view=%s" % params["state"]
    instructions = {
        "template": "reviewlist.html",
        "can_edit": write_access,
        "edit": edit,
        "editlink": "?mode=requests&trip=%d&edit=%s" % (trip.uid, str(not edit).lower()),
        "shoppinglink": "?mode=shopping_list&trip=%d" % (trip.uid),
        "submitdraftlink": "?mode=submit_drafts&trip=%d" % (trip.uid),
        "procurementlink": "?mode=request_procurement_dispatching&trip=%d" % (trip.uid),
        "unloadlink": "?mode=unload_processing&trip=%d" % (trip.uid),
        "count": len(objects),
    }
    return editable_table("Request Review List for " + str(trip.date), columns, rows, instructions=instructions, action=action, optionsets=optionsets, onedit=True, wrap=wrap, addspans=spans)

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
        ("text",                  "quantity.new", "",               ""                   ),
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
    return editable_table("Request Entry Form for " + trip_date, ["Edit?", "Formal Item Name", "Informal Description", "Quantity", "Substitution Requirements", "Cost Object", "Co-op Date", "Comments", "State"], rows, instructions=instructions, creation=creation, action="?mode=request_submit&trip=%d" % trip.uid, optionsets=optionsets, onedit=True)

@mode
def request_results(user, write_access, params):
    items = item_names_by_uids()
    costs = cost_objects_by_uids()
    locations = locations_by_uids()
    trip_dates = {t.uid: t.date for t in db.query(db.ShoppingTrip).all()}

    tripid = int_or_none(params, "trip")
    if tripid is not None and tripid not in trip_dates:
        return {"template": "error.html", "message": "unrecognized trip ID"}

    instructions = {
        "template": "select.html",
        "action": "?mode=request_results",
        "name": "trip",
        "options": [("", "")] + list(trip_dates.items()),
        "selection": tripid,
    }

    if tripid is not None:
        objects = db.query(db.Request).filter_by(contact=user, tripid=tripid).order_by(db.Request.submitted_at).all()
    else:
        objects = db.query(db.Request).filter_by(contact=user).order_by(db.Request.submitted_at).all()
    objects.reverse()

    rows = [
        [
            i.uid,
            get_by_id(items, i.itemid),
            i.description,
            render_quantity(i.quantity, i.unit),
            i.substitution,
            get_by_id(costs, i.costid),
            i.coop_date or "",
            i.comments,
            i.state,
            i.procurement_comments,
            get_by_id(locations, i.procurement_location),
            ("R-%d" % i.uid if i.coop_date is not None else ""),
        ] for i in objects
    ]
    return simple_table("Previous Request Results", ["ID", "Item Name", "Informal Name", "Quantity", "Substitution Requirements", "Cost Object", "Co-op Date", "Comments", "State", "Procurement Comments", "Procurement Location", "Procurement ID"], rows, instructions=instructions)

@mode
def request_procurement_dispatching(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}

    items = item_names_by_uids()
    costs = cost_objects_by_uids()

    locations = {l.uid: l.name for l in db.query(db.Location).all()}
    location_options = [("", "")] + sorted(locations.items())

    objects = db.query(db.Request).filter(db.Request.tripid == tripid, db.Request.state == db.RequestState.accepted, db.Request.coop_date != None).all()

    optionsets = {
        "locations": location_options,
    }

    all_locations = {}
    for i in build_latest_inventory():
        if i.quantity < 0.005: # zero, because two decimal points
            continue
        if i.itemid not in all_locations:
            all_locations[i.itemid] = []
        all_locations[i.itemid].append(i.locationid)
    likely_locations = {k: (locations[v[0]] if len(v) == 1 else "multiple") for k, v in all_locations.items() if len(v) >= 1}

    columns = ["Edit?", "ID", "Likely Location", "Item Name", "Quantity", "Contact", "Cost Object", "State", "Substitution Requirements", "Comments", "Procurement Comments", "Procurement Location"]

    # needs a "likely location" column

    rows = [
        [
            ("checkbox",                           "edit.%d" % i.uid, "",                        False                                ),
            ("",                                                  "", "",                        i.uid                                ),
            ("",                                                  "", "",                        likely_locations.get(i.itemid, "")   ),
            ("",                                                  "", "",                        get_by_id(items, i.itemid)           ),
            ("",                                                  "", "",                        render_quantity(i.quantity, i.unit)  ),
            ("",                                                  "", "",                        i.contact                            ),
            ("",                                                  "", "",                        costs.get(i.costid, "#REF?")         ),
            ("dropdown",                          "state.%d" % i.uid, state_options(i, qm=True), i.state                              ),
            ("",                                                  "", "",                        i.substitution                       ),
            ("",                                                  "", "",                        i.comments                           ),
            ("text",               "procurement_comments.%d" % i.uid, "",                        i.procurement_comments               ),
            ("dropdown-optionset", "procurement_location.%d" % i.uid, "locations",               i.procurement_location               ),
        ] for i in objects
    ]
    rows.sort(key=lambda r: (r[2][3] or "", r[3][3] or ""))
    action = "?mode=request_procurement_update&trip=%d" % trip.uid
    return editable_table("Procurement Dispositioning List for " + str(trip.date), columns, rows, action=action, optionsets=optionsets, onedit=True)

@mode
def unload_processing(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}

    items = item_names_by_uids()
    costs = cost_objects_by_uids()

    locations = {l.uid: l.name for l in db.query(db.Location).all()}
    location_options = [("", "")] + sorted(locations.items())

    objects = db.query(db.Request).filter(db.Request.tripid == tripid, db.Request.state == db.RequestState.to_purchase).all()

    optionsets = {
        "locations": location_options,
    }

    columns = ["Edit?", "ID", "Item Name", "Quantity", "Contact", "Cost Object", "State", "Comments", "Procurement Comments", "Procurement Location"]

    # needs a "likely location" column

    rows = [
        [
            ("checkbox",                           "edit.%d" % i.uid, "",                        False                                ),
            ("",                                                  "", "",                        i.uid                                ),
            ("",                                                  "", "",                        get_by_id(items, i.itemid)           ),
            ("",                                                  "", "",                        render_quantity(i.quantity, i.unit)  ),
            ("",                                                  "", "",                        i.contact                            ),
            ("",                                                  "", "",                        costs.get(i.costid, "#REF?")         ),
            ("dropdown",                          "state.%d" % i.uid, state_options(i, qm=True), i.state                              ),
            ("",                                                  "", "",                        i.comments                           ),
            ("text",               "procurement_comments.%d" % i.uid, "",                        i.procurement_comments               ),
            ("dropdown-optionset", "procurement_location.%d" % i.uid, "locations",               i.procurement_location               ),
        ] for i in objects
    ]
    rows.sort(key=lambda r: (r[2][3] or "", r[3][3] or ""))
    action = "?mode=request_unload_update&trip=%d" % trip.uid
    return editable_table("Unloading Processing List for " + str(trip.date), columns, rows, action=action, optionsets=optionsets, onedit=True)

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

    # TODO: validate locations
    procurement_locationid = int_or_none(params, "procurement_location" + suffix)

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
        procurement_comments = param_as_str(params, "procurement_comments" + suffix, ""),
        procurement_location = procurement_locationid,
    )

def merge_changes(target, source):
    changes = False

    for field in ["itemid", "costid", "description", "unit", "substitution", "comments", "state", "procurement_comments", "procurement_location"]:
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

def handle_request_updates(user, write_access, params, trip, require_edit=False, state_only=False, procurement_only=False):
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

        if state_only or procurement_only:
            state = params.get("state.%d" % request.uid)
            if state is not None and request.state != state:
                if state not in allowable_states(request, qm=write_access):
                    return {"template": "error.html", "message": "invalid state: %s" % repr(state)}
                request.state = state
                any_edits = True
        if procurement_only:
            procurement_comments = params.get("procurement_comments.%d" % request.uid, "")
            if request.procurement_comments != procurement_comments:
                request.procurement_comments = procurement_comments
                any_edits = True
            # TODO: validate that this is a valid location
            procurement_location = int_or_none(params, "procurement_location.%d" % request.uid)
            if request.procurement_location != procurement_location:
                request.procurement_location = procurement_location
                any_edits = True
        elif not state_only:
            updated_request = create_request_from_params(params, ".%d" % request.uid, tripid=trip.uid, contact=user, allowable_cost_ids=allowable_cost_ids, allowable_states=allowable_states(request, qm=write_access))
            if updated_request is None:
                return {"template": "error.html", "message": "attempt to change request to have no item name, formal or informal"}
            if type(updated_request) == str:
                return {"template": "error.html", "message": updated_request}
            if merge_changes(request, updated_request):
                any_edits = True

    if state_only or procurement_only:
        new_request = None
    else:
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
    nparams = {"trip": params["trip"], "edit": "true"}
    if "state_view" in params:
        nparams["state"] = params["state_view"]
    return requests(user, write_access, nparams)

@mode
def request_procurement_update(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}
    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}
    res = handle_request_updates(user, write_access, params, trip, require_edit=True, procurement_only=True)
    if res is not None:
        return res
    nparams = {"trip": params["trip"]}
    return request_procurement_dispatching(user, write_access, nparams)

@mode
def request_unload_update(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}
    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}
    res = handle_request_updates(user, write_access, params, trip, require_edit=True, procurement_only=True)
    if res is not None:
        return res
    nparams = {"trip": params["trip"]}
    return unload_processing(user, write_access, nparams)

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
def purchase_retirement_list(user, write_access, params):
    communal_costids = [co.uid for co in db.query(db.CostObject).filter_by(kerberos=None).all()]
    requests = db.query(db.Request).filter(db.Request.itemid != None, db.Request.state == db.RequestState.purchased, db.Request.costid.in_(communal_costids), db.Request.procurement_location != None).all()

    trip_dates = {t.uid: t.date for t in db.query(db.ShoppingTrip).all()}

    locations = locations_by_uids()
    items = item_names_by_uids()

    # line to represent the request
    rows = [[
        ("",                          "", "", str(r.uid)                         ),
        ("",                          "", "", items[r.itemid]                    ),
        ("",                          "", "", r.comments                         ),
        ("",                          "", "", r.procurement_comments             ),
        ("text",   "quantity.%d" % r.uid, "", render_quantity(r.quantity, r.unit)),
        ("",                          "", "", locations[r.procurement_location]  ),
        ("",                          "", "", trip_dates[r.tripid]               ),
        ("checkbox", "retire.%d" % r.uid, "", ""                                 ),
    ] for r in requests]

    rows.sort(key=lambda r: r[1])

    instructions = "WARNING: anything marked as 'substituted' will not be handled here, and must be reviewed manually! Additionally, not all 'purchased' items are shown here necessarily; confirm that none are left after completing this form."

    return editable_table("Inventory Retirement Form", ["Req ID", "Item Name", "Req Comment", "Procurement Comment", "Req Quantity", "Inventory Location", "Date", "Done?"], rows, action=("?mode=retire_purchase_submit" if write_access else None), instructions=instructions)

@mode
def retire_purchase_submit(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    communal_costids = [co.uid for co in db.query(db.CostObject).filter_by(kerberos=None).all()]
    requests = db.query(db.Request).filter(db.Request.itemid != None, db.Request.state == db.RequestState.purchased, db.Request.costid.in_(communal_costids), db.Request.procurement_location != None).all()
    requests_by_id = {r.uid: r for r in requests}

    trip_dates = {t.uid: t.date for t in db.query(db.ShoppingTrip).all()}

    count = 0
    for p in params:
        parts = p.split(".")
        if parts[0] == "retire" and len(parts) == 2 and parts[1].isdigit() and params[p] == "on":
            req = requests_by_id.get(int(parts[1]))
            if req is None:
                return {"template": "error.html", "message": "could not find request from %s" % p}
            assert req.state == db.RequestState.purchased
            req.state = db.RequestState.unloaded
            count += 1

            qs = params.get("quantity.%d" % req.uid)
            if qs == "none":
                continue

            quantity, unit = parse_quantity(qs)
            if quantity is None:
                return {"template": "error.html", "message": "could not parse quantity"}
            db.add_no_commit(db.Inventory(
                itemid = req.itemid,
                locationid = req.procurement_location,
                quantity = quantity,
                unit = unit,
                measurement = trip_dates[req.tripid],
                full_inventory = False,
            ))
            count += 1

    if count:
        db.commit()

    return purchase_retirement_list(user, write_access, {})

def to_int_or_none(x):
    return int(x) if x.isdigit() else None

@mode
def shopping_list(user, write_access, params):
    tripid = int_or_none(params, "trip")
    if tripid is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(tripid)
    if trip is None:
        return {"template": "error.html", "message": "unrecognized trip ID"}

    items = item_names_by_uids()
    aisles = item_aisles_by_uids()
    costs = cost_objects_by_uids()

    objects = db.query(db.Request).filter_by(tripid=tripid, state=db.RequestState.to_purchase).all()

    rows = [
        [
            ("checkbox", "", "", ""),
            ("", "", "", get_by_id(aisles, i.itemid) or "UNKNOWN"),
            ("", "", "", get_by_id(items, i.itemid)),
            ("", "", "", render_quantity(i.quantity, i.unit)),
            ("", "", "", i.substitution),
            ("", "", "", i.contact),
            ("", "", "", costs.get(i.costid, "#REF?")),
            ("", "", "", i.comments),
        ] for i in objects
    ]
    rows.sort(key=lambda i: (i[1][3].split("?")[0].split(" ")[0].split(".")[0], to_int_or_none(i[1][3].split("?")[0].split(" ")[0].split(".")[-1]) or 100000, i[2], to_int_or_none(i[3][3].split(" ")[0]) or 100000, i[4], i[5], i[6]))
    return editable_table("Shopping List for " + str(trip.date), ["", "Aisle", "Item Name", "Quantity", "Substitution Requirements", "Contact", "Cost Object", "Comments"], rows)

@mode
def review_balances(user, write_access, params):
    objects = db.query(db.CostObject).all()
    transactions = db.query(db.Transaction).all()

    balances = {c.uid: 0 for c in objects}
    for transaction in transactions:
        balances[transaction.debit_id] += transaction.amount
        balances[transaction.credit_id] -= transaction.amount

    if write_access:
        columns = ["Cost Object", "Venmo", "Amount Owed", "Explain", "Split"]
        rows = [
            [
                ("", "", "", i.description),
                ("", "", "", i.venmo),
                ("", "", "", "$%.2f" % balances[i.uid]),
                ("url", "?mode=personal_transactions&impersonate=%s" % i.kerberos, "", "Transactions") if i.kerberos else ("", "", "", ""),
                ("url", "?mode=split_costs&object=%d" % i.uid, "", "Split Balance") if not i.kerberos and balances[i.uid] > 0 else ("", "", "", ""),
            ] for i in objects
        ]
    else:
        columns = ["Cost Object", "Venmo", "Amount Owed"]
        rows = [
            [
                ("", "", "", i.description),
                ("", "", "", i.venmo),
                ("", "", "", "$%.2f" % balances[i.uid]),
            ] for i in objects
        ]
    return editable_table("Balances", columns, rows)

@mode
def review_transactions(user, write_access, params):
    items = item_names_by_uids()
    costs = cost_objects_by_uids()
    transactions = db.query(db.Transaction).all()
    date_by_trip = {st.uid: st.date for st in db.query(db.ShoppingTrip).all()}
    requests = db.query(db.Request).all()
    formal_names = {req.uid: items[req.itemid] for req in requests if req.itemid is not None}

    rows = build_table(
        transactions,
        "uid",
        lambda i: costs.get(i.credit_id, "#REF?"),
        lambda i: costs.get(i.debit_id, "#REF?"),
        lambda i: "$%.2f" % i.amount,
        lambda i: get_by_id(date_by_trip, i.trip_id),
        lambda i: i.request_id or "",
        lambda i: get_by_id(formal_names, i.request_id),
        "description",
        "added_at",
    )
    rows = [[("", "", "", cell) for ci, cell in enumerate(row)] for row in rows]

    cost_objects = sorted(costs.items())
    trips_dropdown = [("", "")] + sorted(date_by_trip.items())

    last = transactions[-1] if transactions else None

    if write_access:
        creation = [
            ("",                  "", "",             ""                                                   ),
            ("dropdown", "credit_id", cost_objects,   last.credit_id if last else ""                       ),
            ("dropdown",  "debit_id", cost_objects,   last.debit_id if last else ""                        ),
            ("text",        "amount", "",             ""                                                   ),
            ("dropdown",   "trip_id", trips_dropdown, last.trip_id if last else ""                         ),
            ("text",    "request_id", "",             ""                                                   ),
            ("",                  "", "",             ""                                                   ),
            ("text",   "description", "",             last.description if last else ""                     ),
            ("",                  "", "",             "now"                                                ),
        ]
    else:
        creation = []

    return editable_table("Transactions", ["ID", "Credit", "Debit", "Amount", "Trip Date", "Request ID", "Item Name", "Description", "Added"], rows, creation=creation, action=("?mode=add_transaction" if write_access else None), autoscroll=(params.get("scroll") == "bottom"))

@mode
def add_transaction(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    allowable_cost_ids = cost_objects_by_uids().keys()

    trip_id = int_or_none(params, "trip_id")
    if trip_id is not None and get_shopping_trip(trip_id) is None:
        return {"template": "error.html", "message": "invalid trip ID"}

    request_id = int_or_none(params, "request_id")
    if request_id is not None:
        found = db.query(db.Request).filter_by(uid=request_id).all()
        if not found:
            return {"template": "error.html", "message": "invalid request ID"}
        if len(found) > 1:
            return {"template": "error.html", "message": "unexpected multiple requests"}
        if found[0].tripid != trip_id:
            return {"template": "error.html", "message": "request did not match specified trip"}

    credit_id, debit_id = int_or_none(params, "credit_id"), int_or_none(params, "debit_id")
    if not credit_id or not debit_id:
        return {"template": "error.html", "message": "no cost ID specified"}
    if credit_id not in allowable_cost_ids or debit_id not in allowable_cost_ids:
        return {"template": "error.html", "message": "attempt to submit under invalid cost ID"}
    if credit_id == debit_id:
        return {"template": "error.html", "message": "attempt to submit with debit ID = credit ID"}

    description = params.get("description").strip()
    if not description:
        return {"template": "error.html", "message": "no valid description found"}

    try:
        amount = round(float(params.get("amount", "0")), 2)
    except ValueError:
        return {"template": "error.html", "message": "no valid number found for amount"}
    if amount <= 0:
        return {"template": "error.html", "message": "amount was invalid, zero, or negative"}

    new_transaction = db.Transaction(
        credit_id = credit_id,
        debit_id = debit_id,
        amount = amount,
        trip_id = trip_id,
        request_id = request_id,
        description = description,
    )
    db.add(new_transaction)

    params["scroll"] = "bottom"
    return review_transactions(user, write_access, params)

@mode
def split_costs(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    date_by_trip = {st.uid: st.date for st in db.query(db.ShoppingTrip).all()}
    trips_dropdown = [("", "")] + sorted(date_by_trip.items())

    objid = int_or_none(params, "object")
    if objid is None:
        return {"template": "error.html", "message": "invalid object ID"}
    split_objs = [co for co in db.query(db.CostObject).filter_by(uid=objid).all()]
    if not split_objs:
        return {"template": "error.html", "message": "no cost object found for user"}
    if len(split_objs) != 1:
        return {"template": "error.html", "message": "more than one cost object found for user"}
    split_obj = split_objs[0]

    transactions = db.query(db.Transaction).filter(db.sqlalchemy.or_(db.Transaction.credit_id == split_obj.uid, db.Transaction.debit_id == split_obj.uid)).all()
    total = sum(i.amount if i.debit_id == split_obj.uid else -i.amount for i in transactions)

    if total <= 0:
        return {"template": "error.html", "message": "no cost to split! (total = $%.2f)" % total}

    split_among = db.query(db.CostObject).filter(db.CostObject.kerberos != None).all()

    instructions = "Splitting $%.2f among selected cost objects" % total

    columns = ["Cost Object", "Include?", "Trip Date"]
    rows = [
        [
            ("",                 "", "",             "All"),
            ("",                 "", "",             ""   ),
            ("dropdown", "trip.all", trips_dropdown, ""   ),
        ],
        [
            ("",                 "", "",             "All"),
            ("",                 "", "",             ""   ),
            ("text", "describe.all", "",             ""   ),
        ],
    ]
    rows += [
        [
            ("",                           "", "", i.description),
            ("checkbox", "include.%d" % i.uid, "", True         ),
            ("",                           "", "", ""           ),
        ] for i in split_among if i.uid != split_obj.uid
    ]

    return editable_table("Splitting costs from cost object " + split_obj.description, columns, rows, instructions=instructions, action="?mode=split_costs_do&object=%d" % objid)

@mode
def split_costs_do(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    objid = int_or_none(params, "object")
    if objid is None:
        return {"template": "error.html", "message": "invalid object ID"}
    split_objs = [co.uid for co in db.query(db.CostObject).filter_by(uid=objid).all()]
    if not split_objs:
        return {"template": "error.html", "message": "no cost object found for user"}
    if len(split_objs) != 1:
        return {"template": "error.html", "message": "more than one cost object found for user"}
    split_obj = split_objs[0]

    trip_id = int_or_none(params, "trip.all")
    if trip_id is not None and get_shopping_trip(trip_id) is None:
        return {"template": "error.html", "message": "invalid trip ID"}

    description = params.get("describe.all", "").strip()
    if not description:
        return {"template": "error.html", "message": "invalid description"}

    transactions = db.query(db.Transaction).filter(db.sqlalchemy.or_(db.Transaction.credit_id == split_obj, db.Transaction.debit_id == split_obj)).all()
    total = sum(i.amount if i.debit_id == split_obj else -i.amount for i in transactions)

    split_among = db.query(db.CostObject).filter(db.CostObject.kerberos != None).all()

    selected_ids = set()
    for param in params:
        if param.startswith("include.") and param[8:].isdigit() and params[param] == "on":
            selected_ids.add(int(param[8:]))
    split_among = [co for co in split_among if co.uid in selected_ids]

    if len(split_among) != len(selected_ids):
        return {"template": "error.html", "message": "invalid selected ID"}

    if len(split_among) < 2:
        return {"template": "error.html", "message": "must select at least two cost objects to split costs between"}

    per_person = (round(total * 100) // len(split_among)) / 100

    if total <= 0 or per_person < 0.01:
        return {"template": "error.html", "message": "not enough cost to split! (total = $%.2f)" % total}

    credit_id = split_obj
    for obj in split_among:
        debit_id = obj.uid

        new_transaction = db.Transaction(
            credit_id = credit_id,
            debit_id = debit_id,
            amount = per_person,
            trip_id = trip_id,
            request_id = None,
            description = description,
        )
        db.add_no_commit(new_transaction)

    db.commit()

    params["scroll"] = "bottom"
    return review_transactions(user, write_access, params)

@mode
def personal_transactions(user, write_access, params):
    items = item_names_by_uids()
    costs = cost_objects_by_uids()

    user_uids = [co.uid for co in db.query(db.CostObject).filter_by(kerberos=user).all()]
    if not user_uids:
        return {"template": "error.html", "message": "no cost object found for user"}
    if len(user_uids) != 1:
        return {"template": "error.html", "message": "more than one cost object found for user"}
    user_id = user_uids[0]

    transactions = db.query(db.Transaction).filter(db.sqlalchemy.or_(db.Transaction.credit_id == user_id, db.Transaction.debit_id == user_id)).all()
    date_by_trip = {st.uid: st.date for st in db.query(db.ShoppingTrip).all()}
    requests = db.query(db.Request).all()
    formal_names = {req.uid: items[req.itemid] for req in requests if req.itemid is not None}
    total = sum(i.amount if i.debit_id == user_id else -i.amount for i in transactions)

    rows = build_table(
        transactions,
        "uid",
        lambda i: (costs.get(i.credit_id, "#REF?") if i.debit_id == user_id else costs.get(i.debit_id, "#REF?")),
        lambda i: "$%.2f" % (i.amount if i.debit_id == user_id else -i.amount),
        lambda i: get_by_id(date_by_trip, i.trip_id),
        lambda i: i.request_id or "",
        lambda i: get_by_id(formal_names, i.request_id),
        "description",
        "added_at",
    )
    if total > 0:
        rows += [("", "Owed BY you:", "$%.2f" % total, "", "", "", "", "")]
    else:
        rows += [("", "Owed TO you:", "$%.2f" % -total, "", "", "", "", "")]

    instructions = "If you have an outstanding balance, please send it via Venmo to %s." % QM_VENMO

    return simple_table("Personal Transactions for " + user, ["ID", "Account", "Amount", "Trip Date", "Request ID", "Item Name", "Description", "Added"], rows, instructions=instructions)

@mode
def submit_drafts(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    trip_id = int_or_none(params, "trip")
    if trip_id is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(trip_id)
    if trip is None:
        return {"template": "error.html", "message": "trip ID not found"}

    count = db.query(db.Request).filter_by(tripid=trip_id, state=db.RequestState.draft).count()

    return {"template": "confirm.html", "instructions": "Are you certain that you want to submit all %d drafts for the shopping trip on %s?" % (count, trip.date), "action": "?mode=submit_drafts_confirmed&trip=%d" % trip_id, "cancel": "?mode=requests&trip=%d" % trip_id}

@mode
def submit_drafts_confirmed(user, write_access, params):
    if not write_access:
        return {"template": "error.html", "message": "no QM access"}

    trip_id = int_or_none(params, "trip")
    if trip_id is None:
        return {"template": "error.html", "message": "invalid trip ID"}
    trip = get_shopping_trip(trip_id)
    if trip is None:
        return {"template": "error.html", "message": "trip ID not found"}

    for request in db.query(db.Request).filter_by(tripid=trip_id, state=db.RequestState.draft).all():
        request.state = db.RequestState.submitted
    db.commit()

    return requests(user, write_access, {"trip": params["trip"]})

@mode
def coop_item_summary(user, write_access, params):
    columns = ["Trip", "Co-Op Date", "Item Count"]

    trip_dates = {t.uid: t.date for t in db.query(db.ShoppingTrip).all()}

    stats = {}
    for r in db.query(db.Request).filter(db.Request.coop_date != None, ~db.Request.state.in_([db.RequestState.rejected, db.RequestState.retracted])).all():
        k = (r.tripid, r.coop_date)
        stats[k] = stats.get(k,0) + 1

    rows = [
        [
            trip_dates[tripid],
            coop_date,
            count
        ] for (tripid, coop_date), count in stats.items()
    ]
    rows.sort()

    return simple_table("Co-op Request Summary", columns, rows)

@mode
def all_communal_requests(user, write_access, params):
    items = item_names_by_uids()
    costs = cost_objects_by_uids()

    communal_costids = [co.uid for co in db.query(db.CostObject).filter_by(kerberos=None).all()]
    locations = {l.uid: l.name for l in db.query(db.Location).all()}
    objects = db.query(db.Request).filter(db.Request.costid.in_(communal_costids), db.Request.coop_date == None, ~db.Request.state.in_([db.RequestState.retracted, db.RequestState.rejected, db.RequestState.unavailable, db.RequestState.draft, db.RequestState.submitted])).all()

    columns = ["ID", "Item Name", "Quantity", "Cost Object", "State", "Procurement Comments", "Procurement Location"]
    rows = [
        [
            i.uid,
            get_by_id(items, i.itemid),
            render_quantity(i.quantity, i.unit),
            costs.get(i.costid, "#REF?"),
            i.state,
            i.procurement_comments,
            get_by_id(locations, i.procurement_location),
        ] for i in objects
    ]
    rows.reverse()

    if len({r.costid for r in objects}) == 1:
        # remove cost ID column if only one option used (the common case)
        del columns[3]
        for row in rows:
            del row[3]

    return simple_table("Complete Communal Request List", columns, rows)

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

    if write_access and params.get("impersonate"):
        user = params["impersonate"]

    if param_as_str(params, "act") == "mortal":
        write_access = False

    return modes[mode](user, write_access, params)

def render(results):
    return jenv.get_template(results["template"]).render(**results)

def print_index():
    page = render(process_index())

    print("Content-type: text/html\n")
    print(page)

if __name__ == "__main__":
    print_index()
