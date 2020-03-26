import os
import sqlalchemy
import sqlalchemy.ext.declarative
import sqlalchemy.orm
import enum

SQLBase = sqlalchemy.ext.declarative.declarative_base()

class ItemType(SQLBase):
    __tablename__ = "item_type"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    name = sqlalchemy.Column(sqlalchemy.String(127), nullable=False)
    standard_unit = sqlalchemy.Column(sqlalchemy.String(15), nullable=False)

class Location(SQLBase):
    __tablename__ = "location"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    name = sqlalchemy.Column(sqlalchemy.String(127), nullable=False)

class Inventory(SQLBase):
    __tablename__ = "inventory"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    itemid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    quantity = sqlalchemy.Column(sqlalchemy.DECIMAL(18, 2), nullable=False)
    unit = sqlalchemy.Column(sqlalchemy.String(15), nullable=False)
    locationid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    measurement = sqlalchemy.Column(sqlalchemy.Date(), nullable=False)

class CostObject(SQLBase):
    __tablename__ = "cost_object"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    description = sqlalchemy.Column(sqlalchemy.String(127), nullable=False)
    kerberos = sqlalchemy.Column(sqlalchemy.String(8), nullable=True)
    venmo = sqlalchemy.Column(sqlalchemy.String(63), nullable=True)

class ShoppingTrip(SQLBase):
    __tablename__ = "shopping_trip"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    date = sqlalchemy.Column(sqlalchemy.Date(), nullable=False)

class RequestState(enum.Enum):
    draft = 1
    submitted = 2
    accepted = 3
    to_purchase = 4
    to_reserve = 5
    in_inventory = 6
    retracted = 7
    rejected = 8

class Request(SQLBase):
    __tablename__ = "request"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    tripid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    itemid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=True)
    costid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    description = sqlalchemy.Column(sqlalchemy.String(127), nullable=True)
    quantity = sqlalchemy.Column(sqlalchemy.DECIMAL(18, 2), nullable=True)
    unit = sqlalchemy.Column(sqlalchemy.String(15), nullable=True)
    substitution = sqlalchemy.Column(sqlalchemy.String(255), nullable=False)
    contact = sqlalchemy.Column(sqlalchemy.String(127), nullable=False)
    coop_date = sqlalchemy.Column(sqlalchemy.Date(), nullable=True)
    comments = sqlalchemy.Column(sqlalchemy.String(255), nullable=False)
    submitted_at = sqlalchemy.Column(sqlalchemy.DateTime(), nullable=False)
    updated_at = sqlalchemy.Column(sqlalchemy.DateTime(), nullable=False)
    state = sqlalchemy.Column(sqlalchemy.Enum(RequestState), nullable=False)

#class AisleInfo(SQLBase):
#    __tablename__ == "aisle_info"
#    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
#    aisle = sqlalchemy.Column(sqlalchemy.String(63), nullable=False)

#class AisleDisposition(enum.Enum):
#    most_likely = 1
#    alternate = 2
#    confirmed = 3
#    other = 4

#class AisleAvailability(SQLBase):
#    __tablename__ = "aisle_availability"
#    uid = sqlalchemy.Colunm(sqlalchemy.Integer(), nullable=False, primary_key=True)
#    itemid = sqlalchemy.Colunm(sqlalchemy.Integer(), nullable=False)
#    aisleid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
#    disposition = sqlalchemy.Column(sqlalchemy.Enum(AisleDisposition), nullable=False)

#class ShoppingIntention(SQLBase):
#    __tablename__ == "shopping_intention"
#    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
#    tripid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
#    itemid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
#    quantity = sqlalchemy.Column(sqlalchemy.DECIMAL(18, 2), nullable=False)
#    unit = sqlalchemy.Column(sqlalchemy.String(15), nullable=False)
#    substitutions = sqlalchemy.Column(sqlalchemy.Text(), nullable=False)
#    notes = sqlalchemy.Column(sqlalchemy.Text(), nullable=False)

#class ShoppingResult(SQLBase):
#    __tablename__ == "shopping_intention"
#    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
#    tripid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
#    intentionid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=True)
#    quantity = sqlalchemy.Column(sqlalchemy.DECIMAL(18, 2), nullable=False)
#    unit = sqlalchemy.Column(sqlalchemy.String(15), nullable=False)
#    cost_total_dollars = sqlalchemy.Column(sqlalchemy.DECIMAL(18, 2), nullable=False)

with open(os.path.join(os.getenv("HOME"), ".my.cnf")) as f:
    password = dict(line.strip().split("=") for line in f if line.count("=") == 1)["password"]

sqlengine = sqlalchemy.create_engine("mysql://cela:%s@sql.mit.edu/cela+qazoo" % password)
SQLBase.metadata.bind = sqlengine

session = sqlalchemy.orm.sessionmaker(bind=sqlengine)()

def query(x):
    return session.query(x)

#def get_all_racks():
#    return session.query(Racks).all()

#def get_all_devices():
#    devices = []
#    seen = set()
#    for device in session.query(DeviceUpdates).order_by(DeviceUpdates.txid.desc()).all():
#        if device.id in seen:
#            continue
#        seen.add(device.id)
#        devices.append(device)
#    return devices

#def get_all_parts():
#    return session.query(Parts).all()

#def get_latest_inventory():
#    # TODO: do this more efficiently
#    updates = session.query(Inventory).all()
#    latest = {}
#    for update in updates:
#        if update.sku not in latest or latest[update.sku].txid < update.txid:
#            latest[update.sku] = update
#    return latest

#def get_inventory(sku):
#    return session.query(Inventory).filter_by(sku=sku).all()

#def get_part(sku):
#    return session.query(Parts).filter_by(sku=sku).one()

#def get_rack(name):
#    return session.query(Racks).filter_by(name=name).one()

# latest version is at the end of the returned list
#def get_device_history(id):
#    return session.query(DeviceUpdates).filter_by(id=id).order_by(DeviceUpdates.txid).all()

#def get_device_latest(id):
#    return session.query(DeviceUpdates).filter_by(id=id).order_by(DeviceUpdates.txid.desc()).limit(1).one()

def add(x):
    session.add(x)
    session.commit()

