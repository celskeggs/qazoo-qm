import os
import sqlalchemy
import sqlalchemy.ext.declarative
import sqlalchemy.orm

SQLBase = sqlalchemy.ext.declarative.declarative_base()

# TODO: use ORM relations

class ItemType(SQLBase):
    __tablename__ = "item_type"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    name = sqlalchemy.Column(sqlalchemy.String(127), nullable=False)
    standard_unit = sqlalchemy.Column(sqlalchemy.String(63), nullable=False)
    aisle = sqlalchemy.Column(sqlalchemy.String(63), nullable=True)

class Location(SQLBase):
    __tablename__ = "location"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    name = sqlalchemy.Column(sqlalchemy.String(127), nullable=False)

class Inventory(SQLBase):
    __tablename__ = "inventory"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    itemid = sqlalchemy.Column(sqlalchemy.Integer(), sqlalchemy.ForeignKey("item_type.uid"), nullable=False)
    item = sqlalchemy.orm.relationship("ItemType")
    quantity = sqlalchemy.Column(sqlalchemy.DECIMAL(18, 2), nullable=False)
    unit = sqlalchemy.Column(sqlalchemy.String(63), nullable=False)
    locationid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    measurement = sqlalchemy.Column(sqlalchemy.Date(), nullable=False)
    full_inventory = sqlalchemy.Column(sqlalchemy.Boolean(), nullable=False)

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
    primary = sqlalchemy.Column(sqlalchemy.Boolean(), nullable=False)

class RequestState:
    draft = "draft"
    submitted = "submitted"
    accepted = "accepted"
    to_purchase = "to_purchase"
    to_reserve = "to_reserve"
    deduplicated = "deduplicated"
    retracted = "retracted"
    rejected = "rejected"
    purchased = "purchased"
    unloaded = "unloaded"
    substituted = "substituted"
    unavailable = "unavailable"
    VALUES = [draft, submitted, accepted, to_purchase, to_reserve, deduplicated, retracted, rejected, purchased, unloaded, unavailable, substituted]
    # allowable new states by "old state" and then "is QM"
    ALLOWABLE = {
        draft: [
            [submitted, retracted],
            [submitted, retracted, rejected],
        ],
        submitted: [
            [draft, retracted],
            [draft, retracted, rejected, accepted],
        ],
        accepted: [
            [],
            [submitted, rejected, to_purchase, to_reserve, deduplicated],
        ],
        to_purchase: [
            [],
            [rejected, accepted, to_reserve, deduplicated, unavailable, purchased, substituted],
        ],
        to_reserve: [
            [],
            [rejected, accepted, to_purchase, deduplicated],
        ],
        deduplicated: [
            [],
            [rejected, accepted, to_purchase, to_reserve],
        ],
        retracted: [
            [],
            [draft, submitted],
        ],
        rejected: [
            [],
            [draft, submitted],
        ],
        unavailable: [
            [],
            [purchased, substituted, to_purchase],
        ],
        purchased: [
            [],
            [unavailable, substituted, to_purchase, unloaded],
        ],
        unloaded: [
            [],
            [purchased],
        ],
        substituted: [
            [],
            [unavailable, purchased, to_purchase],
        ],
    }

class Request(SQLBase):
    __tablename__ = "request"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    tripid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    itemid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=True)
    costid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    description = sqlalchemy.Column(sqlalchemy.String(127), nullable=True)
    quantity = sqlalchemy.Column(sqlalchemy.DECIMAL(18, 2), nullable=True)
    unit = sqlalchemy.Column(sqlalchemy.String(63), nullable=True)
    substitution = sqlalchemy.Column(sqlalchemy.String(255), nullable=False)
    contact = sqlalchemy.Column(sqlalchemy.String(127), nullable=False)
    coop_date = sqlalchemy.Column(sqlalchemy.Date(), nullable=True)
    comments = sqlalchemy.Column(sqlalchemy.String(255), nullable=False)
    submitted_at = sqlalchemy.Column(sqlalchemy.DateTime(), nullable=False)
    updated_at = sqlalchemy.Column(sqlalchemy.DateTime(), nullable=False)
    state = sqlalchemy.Column(sqlalchemy.Enum(*RequestState.VALUES), nullable=False)
    procurement_comments = sqlalchemy.Column(sqlalchemy.String(255), nullable=False)
    procurement_location = sqlalchemy.Column(sqlalchemy.Integer(), nullable=True)

class Reservation(SQLBase):
    __tablename__ = "reservation"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    until = sqlalchemy.Column(sqlalchemy.Date(), nullable=False)
    itemid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    locationid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    quantity = sqlalchemy.Column(sqlalchemy.DECIMAL(18, 2), nullable=False)
    unit = sqlalchemy.Column(sqlalchemy.String(63), nullable=False)

class Transaction(SQLBase):
    __tablename__ = "transaction"
    uid = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False, primary_key=True)
    credit_id = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    debit_id = sqlalchemy.Column(sqlalchemy.Integer(), nullable=False)
    amount = sqlalchemy.Column(sqlalchemy.DECIMAL(6, 2), nullable=False)
    trip_id = sqlalchemy.Column(sqlalchemy.Integer(), nullable=True)
    request_id = sqlalchemy.Column(sqlalchemy.Integer(), nullable=True)
    description = sqlalchemy.Column(sqlalchemy.String(255), nullable=False)
    added_at = sqlalchemy.Column(sqlalchemy.TIMESTAMP(), nullable=False)

with open(os.path.join(os.getenv("HOME"), ".my.cnf")) as f:
    password = dict(line.strip().split("=") for line in f if line.count("=") == 1)["password"]

sqlengine = sqlalchemy.create_engine("mysql://cela:%s@sql.mit.edu/cela+qazoo" % password)
SQLBase.metadata.bind = sqlengine

session = sqlalchemy.orm.sessionmaker(bind=sqlengine)()

def query(x):
    return session.query(x)

def add_no_commit(x):
    session.add(x)

def add(x):
    session.add(x)
    session.commit()

def commit():
    session.commit()

