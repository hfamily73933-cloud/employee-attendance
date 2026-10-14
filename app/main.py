"""Employee Attendance & Analytics API. Run: uvicorn app.main:app --port 8000"""

import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from bson import Binary, ObjectId
from bson.timestamp import Timestamp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()
client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    serverSelectionTimeoutMS=5000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def create_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)], unique=True, name="uq_employee_code"
    )
    db.employees.create_index(
        [("department", ASCENDING), ("emp_code", ASCENDING)],
        name="ix_employee_department_code",
    )
    db.employees.create_index(
        [("joined_on", ASCENDING)], name="ix_employee_joined_on"
    )
    db.employees.create_index(
        [("department", ASCENDING), ("joined_on", ASCENDING)],
        name="ix_employee_department_joined",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_employee_date",
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="ix_attendance_date_employee",
    )
    db.attendance_logs.create_index(
        [("status", ASCENDING), ("date", DESCENDING)],
        name="ix_attendance_status_date",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_indexes()
    yield
    client.close()


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


def truncate_seconds(dt: datetime) -> datetime:
    return dt.replace(microsecond=0)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_ist(dt: datetime) -> datetime:
    return to_utc(dt).astimezone(IST)


def epoch_ms(dt: datetime | None) -> int | None:
    return None if dt is None else int(to_utc(dt).timestamp() * 1000)


def parse_epoch_ms(value: int | None) -> datetime:
    dt = datetime.now(UTC) if value is None else datetime.fromtimestamp(value / 1000, tz=UTC)
    return truncate_seconds(dt)


def attendance_date_for(punch_in: datetime, employee: dict) -> str:
    local = to_ist(punch_in)
    if (
        employee["shift_end"] <= employee["shift_start"]
        and local.strftime("%H:%M") < employee["shift_end"]
    ):
        local -= timedelta(days=1)
    return local.date().isoformat()


def compute_late_minutes(
    punch_in: datetime, shift_start: str, attendance_date: str | None = None
) -> int:
    local = to_ist(punch_in)
    shift_date = date.fromisoformat(attendance_date) if attendance_date else local.date()
    hours, minutes = map(int, shift_start.split(":"))
    start = datetime(
        shift_date.year, shift_date.month, shift_date.day, hours, minutes, tzinfo=IST
    )
    elapsed_seconds = int((local - start).total_seconds())
    return elapsed_seconds // 60 if elapsed_seconds > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = int(
        (truncate_seconds(to_utc(punch_out)) - truncate_seconds(to_utc(punch_in))).total_seconds()
    )
    return float(
        (Decimal(seconds) / Decimal(3600)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )


def compute_overtime(
    punch_out: datetime, shift_end: str, date_str: str, shift_start: str
) -> int:
    shift_date = date.fromisoformat(date_str)
    end_hour, end_minute = map(int, shift_end.split(":"))
    start_hour, start_minute = map(int, shift_start.split(":"))
    if (end_hour, end_minute) <= (start_hour, start_minute):
        shift_date += timedelta(days=1)
    end_dt = datetime(
        shift_date.year, shift_date.month, shift_date.day,
        end_hour, end_minute, tzinfo=IST,
    )
    minutes = int((to_ist(punch_out) - end_dt).total_seconds()) // 60
    return minutes if minutes >= 30 else 0


def serialize_employee(doc: dict) -> dict:
    result = {key: value for key, value in doc.items() if key != "_id"}
    if "created_at" in result:
        result["created_at"] = epoch_ms(result["created_at"])
    return result


def serialize_attendance(doc: dict) -> dict:
    result = {key: value for key, value in doc.items() if key != "_id"}
    result["punch_in"] = epoch_ms(result.get("punch_in"))
    result["punch_out"] = epoch_ms(result.get("punch_out"))
    result.setdefault("work_hours", None)
    result.setdefault("late_minutes", 0)
    result.setdefault("overtime_minutes", 0)
    result.setdefault("half_day", False)
    history = []
    for entry in result.get("history", []):
        item = dict(entry)
        item["at"] = epoch_ms(item.get("at"))
        changes = {}
        for field, change in item.get("changes", {}).items():
            change = dict(change)
            if field in ("punch_in", "punch_out"):
                change["from"] = epoch_ms(change.get("from"))
                change["to"] = epoch_ms(change.get("to"))
            changes[field] = change
        item["changes"] = changes
        history.append(item)
    result["history"] = history
    return result


def make_json_safe(value):
    """Convert BSON-specific values into plain JSON-compatible Python values."""
    if isinstance(value, Timestamp):
        return {"time": value.time, "inc": value.inc}
    if isinstance(value, (bytes, Binary)):
        return value.hex()
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(item) for item in value]
    return value


class EmployeeIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=120)
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: date

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email address")
        return value

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    emp_code: str = Field(min_length=1)
    punched_at: StrictInt | None = Field(
        default=None, ge=100_000_000_000, le=4_102_444_800_000
    )
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str = Field(min_length=1)
    punched_at: StrictInt | None = Field(
        default=None, ge=100_000_000_000, le=4_102_444_800_000
    )


class RegularizeIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"] | None = None
    punch_in: StrictInt | None = Field(
        default=None, ge=100_000_000_000, le=4_102_444_800_000
    )
    punch_out: StrictInt | None = Field(
        default=None, ge=100_000_000_000, le=4_102_444_800_000
    )
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def validate_optional_timestamp(cls, value):
        if value is not None:
            parse_epoch_ms(value)
        return value


@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "MongoDB is unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["joined_on"] = body.joined_on.isoformat()
    doc["created_at"] = truncate_seconds(datetime.now(UTC))
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(
    department: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {} if department is None else {"department": department}
    total = db.employees.count_documents(query)
    docs = (
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [serialize_employee(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    punched_at = parse_epoch_ms(body.punched_at)
    day = attendance_date_for(punched_at, employee)
    doc = {
        "emp_code": body.emp_code,
        "date": day,
        "status": body.status,
        "punch_in": punched_at,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(punched_at, employee["shift_start"], day),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return serialize_attendance(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    punched_at = parse_epoch_ms(body.punched_at)
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$ne": None, "$lte": punched_at}},
        sort=[("punch_in", DESCENDING)],
    )
    if record is None:
        raise HTTPException(404, "No punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")
    punch_in_time = to_utc(record["punch_in"])
    if punched_at <= punch_in_time:
        raise HTTPException(422, "Punch-out must be after punch-in")
    if punched_at - punch_in_time > timedelta(hours=24):
        raise HTTPException(422, "Punch-out cannot be more than 24 hours after punch-in")
    work_hours = compute_work_hours(punch_in_time, punched_at)
    overtime = compute_overtime(
        punched_at, employee["shift_end"], record["date"], employee["shift_start"]
    )
    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": punched_at,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": work_hours < 4.50,
            }
        },
    )
    if result.modified_count != 1:
        raise HTTPException(409, "Already punched out")
    return serialize_attendance(db.attendance_logs.find_one({"_id": record["_id"]}))


@app.patch("/attendance/{emp_code}/{record_date}")
def regularize_attendance(emp_code: str, record_date: date, body: RegularizeIn):
    supplied = body.model_fields_set
    if "status" in supplied and body.status is None:
        raise HTTPException(422, "status cannot be null")
    if "punch_in" in supplied and body.punch_in is None:
        raise HTTPException(422, "punch_in cannot be null; omit it to leave unchanged")
    if "punch_out" in supplied and body.punch_out is None:
        raise HTTPException(422, "punch_out cannot be null; omit it to leave unchanged")

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    day = record_date.isoformat()
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": day})
    if record is None:
        raise HTTPException(404, "Attendance record not found")

    old_status = record.get("status")
    old_in = to_utc(record["punch_in"]) if record.get("punch_in") is not None else None
    old_out = to_utc(record["punch_out"]) if record.get("punch_out") is not None else None
    new_status = body.status if body.status is not None else old_status
    new_in = parse_epoch_ms(body.punch_in) if body.punch_in is not None else old_in
    new_out = parse_epoch_ms(body.punch_out) if body.punch_out is not None else old_out

    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "ABSENT and LEAVE cannot be supplied with punch times")
        new_in = new_out = None
    elif new_in is None:
        raise HTTPException(422, "A presence status requires punch_in")

    if new_in is not None and attendance_date_for(new_in, employee) != day:
        raise HTTPException(422, "punch_in must belong to the attendance date")
    if new_out is not None:
        if new_in is None or new_out <= new_in:
            raise HTTPException(422, "punch_out must be after punch_in")
        if new_out - new_in > timedelta(hours=24):
            raise HTTPException(422, "punch_out cannot be more than 24 hours after punch-in")
    if new_status == old_status and new_in == old_in and new_out == old_out:
        raise HTTPException(422, "Request changes nothing")

    late = compute_late_minutes(new_in, employee["shift_start"], day) if new_in else 0
    if new_in is not None and new_out is not None:
        work_hours = compute_work_hours(new_in, new_out)
        overtime = compute_overtime(
            new_out, employee["shift_end"], day, employee["shift_start"]
        )
        half_day = work_hours < 4.50
    else:
        work_hours, overtime, half_day = None, 0, False

    old_values = {
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }
    new_values = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "work_hours": work_hours,
        "late_minutes": late,
        "overtime_minutes": overtime,
        "half_day": half_day,
    }
    changes = {
        field: {"from": old_value, "to": new_values[field]}
        for field, old_value in old_values.items()
        if old_value != new_values[field]
    }
    audit = {
        "at": parse_epoch_ms(None),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": record.get("status"),
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
        },
        {"$set": new_values, "$push": {"history": audit}},
    )
    if result.modified_count != 1:
        raise HTTPException(409, "Attendance record changed concurrently; retry the correction")
    return serialize_attendance(db.attendance_logs.find_one({"emp_code": emp_code, "date": day}))


@app.get("/attendance")
def list_attendance(
    emp_code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    query = {}
    if emp_code:
        query["emp_code"] = emp_code
    if date_from or date_to:
        bounds = {}
        if date_from:
            bounds["$gte"] = date_from.isoformat()
        if date_to:
            bounds["$lte"] = date_to.isoformat()
        query["date"] = bounds
    if status:
        query["status"] = status
    total = db.attendance_logs.count_documents(query)
    docs = (
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [serialize_attendance(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def month_bounds(month: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "month must use YYYY-MM format")
    start = date.fromisoformat(f"{month}-01")
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    employee = db.employees.find_one(
        {"emp_code": emp_code}, {"_id": 0, "emp_code": 1, "joined_on": 1}
    )
    if employee is None:
        raise HTTPException(404, "Employee not found")
    month_start = date.fromisoformat(f"{month}-01")
    start_text, end_text = month_bounds(month)
    pipeline = [
        {"$match": {"emp_code": emp_code}},
        {"$addFields": {
            "_joined": {"$dateFromString": {"dateString": "$joined_on"}},
            "_month_start": {"$dateFromString": {"dateString": start_text}},
            "_month_end": {"$dateFromString": {"dateString": end_text}},
        }},
        {"$addFields": {"_working_start": {"$max": ["$_joined", "$_month_start"]}}},
        {"$addFields": {"_day_count": {"$max": [0, {"$dateDiff": {
            "startDate": "$_working_start", "endDate": "$_month_end", "unit": "day"
        }}]}}},
        {"$addFields": {"working_days": {"$size": {"$filter": {
            "input": {"$map": {
                "input": {"$range": [0, "$_day_count"]},
                "as": "offset",
                "in": {"$dateAdd": {
                    "startDate": "$_working_start", "unit": "day", "amount": "$$offset"
                }},
            }},
            "as": "day",
            "cond": {"$and": [
                {"$gte": [{"$dayOfWeek": "$$day"}, 2]},
                {"$lte": [{"$dayOfWeek": "$$day"}, 6]},
            ]},
        }}}}},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code", "start": {"$literal": start_text}, "end": {"$literal": end_text}},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$emp_code", "$$code"]},
                    {"$gte": ["$date", "$$start"]},
                    {"$lt": ["$date", "$$end"]},
                ]}}},
                {"$group": {
                    "_id": None,
                    "present_days": {"$sum": {"$cond": [{"$and": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]},
                        {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]},
                    ]}, {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}},
                    "leave_days": {"$sum": {"$cond": [{"$and": [
                        {"$eq": ["$status", "LEAVE"]},
                        {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]},
                        {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]},
                    ]}, 1, 0]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
                    "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                    "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
                }},
            ],
            "as": "_stats",
        }},
        {"$addFields": {"_stats": {"$ifNull": [{"$arrayElemAt": ["$_stats", 0]}, {
            "present_days": 0, "leave_days": 0, "late_count": 0,
            "total_late_minutes": 0, "total_overtime_minutes": 0,
        }]}}},
        {"$addFields": {"attendance_pct": {"$cond": [
            {"$eq": ["$working_days", 0]},
            None,
            {"$divide": [{"$floor": {"$add": [
                {"$multiply": [{"$divide": ["$_stats.present_days", "$working_days"]}, 10000]},
                0.5,
            ]}}, 100]},
        ]}}},
        {"$project": {
            "_id": 0, "emp_code": 1, "month": {"$literal": month},
            "working_days": 1, "present_days": "$_stats.present_days",
            "leave_days": "$_stats.leave_days", "late_count": "$_stats.late_count",
            "total_late_minutes": "$_stats.total_late_minutes",
            "total_overtime_minutes": "$_stats.total_overtime_minutes",
            "attendance_pct": 1,
        }},
    ]
    result = next(db.employees.aggregate(pipeline), None)
    if result is None:
        raise HTTPException(404, "Employee not found")
    return result


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: str | None = None,
):
    start_text, end_text = month_bounds(month)
    employee_match = {"joined_on": {"$lt": end_text}}
    if department is not None:
        employee_match["department"] = department
    pipeline = [
        {"$match": employee_match},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$emp_code", "$$code"]},
                    {"$gte": ["$date", start_text]},
                    {"$lt": ["$date", end_text]},
                ]}}},
                {"$group": {
                    "_id": None,
                    "present_days": {"$sum": {"$cond": [{"$and": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]},
                        {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]},
                    ]}, {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}},
                    "work_hours_sum": {"$sum": {"$cond": [{"$and": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$ne": ["$work_hours", None]},
                    ]}, "$work_hours", 0]}},
                    "work_hours_count": {"$sum": {"$cond": [{"$and": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$ne": ["$work_hours", None]},
                    ]}, 1, 0]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
                    "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                    "leave_count": {"$sum": {"$cond": [{"$and": [
                        {"$eq": ["$status", "LEAVE"]},
                        {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]},
                        {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]},
                    ]}, 1, 0]}},
                    "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
                }},
            ],
            "as": "stats",
        }},
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1},
            "present_days": {"$sum": {"$ifNull": [{"$arrayElemAt": ["$stats.present_days", 0]}, 0]}},
            "work_hours_sum": {"$sum": {"$ifNull": [{"$arrayElemAt": ["$stats.work_hours_sum", 0]}, 0]}},
            "work_hours_count": {"$sum": {"$ifNull": [{"$arrayElemAt": ["$stats.work_hours_count", 0]}, 0]}},
            "late_count": {"$sum": {"$ifNull": [{"$arrayElemAt": ["$stats.late_count", 0]}, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": [{"$arrayElemAt": ["$stats.total_late_minutes", 0]}, 0]}},
            "leave_count": {"$sum": {"$ifNull": [{"$arrayElemAt": ["$stats.leave_count", 0]}, 0]}},
            "on_duty_count": {"$sum": {"$ifNull": [{"$arrayElemAt": ["$stats.on_duty_count", 0]}, 0]}},
        }},
        {"$project": {
            "_id": 0, "department": "$_id", "headcount": 1, "present_days": 1,
            "avg_work_hours": {"$cond": [
                {"$eq": ["$work_hours_count", 0]},
                None,
                {"$divide": [{"$floor": {"$add": [
                    {"$multiply": [{"$divide": ["$work_hours_sum", "$work_hours_count"]}, 100]},
                    0.5,
                ]}}, 100]},
            ]},
            "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1,
        }},
        {"$sort": {"department": 1}},
    ]
    return {"month": month, "items": list(db.employees.aggregate(pipeline))}


@app.get("/analytics/leaderboard/late")
def late_arrival_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: str | None = None,
):
    start_text, end_text = month_bounds(month)
    pipeline = [
        {"$match": {
            "date": {"$gte": start_text, "$lt": end_text},
            "late_minutes": {"$gt": 0},
        }},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "employee",
        }},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}},
        }},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$limit": limit},
        {"$project": {
            "_id": 0, "rank": 1, "emp_code": "$_id",
            "name": "$employee.name", "department": "$employee.department",
            "total_late_minutes": 1, "late_count": 1,
        }},
    ])
    return {
        "month": month,
        "limit": limit,
        "department": department,
        "items": list(db.attendance_logs.aggregate(pipeline)),
    }


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
):
    if to_date < from_date:
        raise HTTPException(422, "'to' must be on or after 'from'")
    if (to_date - from_date).days + 1 > 92:
        raise HTTPException(422, "Date range cannot exceed 92 days")
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(404, "Department not found")

    start_text, end_text = from_date.isoformat(), to_date.isoformat()
    pipeline = [
        {"$match": {"department": department, "joined_on": {"$lte": end_text}}},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$code"]},
                    "date": {"$gte": start_text, "$lte": end_text},
                }},
                {"$project": {
                    "_id": 0, "date": 1, "status": 1, "half_day": 1, "late_minutes": 1,
                }},
            ],
            "as": "logs",
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$project": {
            "_id": 0,
            "date": {"$dateFromString": {"dateString": {"$ifNull": ["$logs.date", start_text]}}},
            "emp_code": 1, "joined_on": 1, "status": "$logs.status",
            "half_day": "$logs.half_day", "late_minutes": "$logs.late_minutes",
            "has_log": {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},
        }},
        {"$group": {
            "_id": "$date",
            "present_count": {"$sum": {"$cond": [
                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                0,
            ]}},
            "late_count": {"$sum": {"$cond": [
                {"$and": [
                    {"$eq": ["$has_log", True]},
                    {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                ]},
                1, 0,
            ]}},
        }},
        {"$densify": {
            "field": "_id",
            "range": {
                "step": 1, "unit": "day",
                "bounds": [
                    datetime.combine(from_date, datetime.min.time()),
                    datetime.combine(to_date + timedelta(days=1), datetime.min.time()),
                ],
            },
        }},
        {"$set": {
            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$_id"}},
            "is_working_day": {"$and": [
                {"$gte": [{"$dayOfWeek": "$_id"}, 2]},
                {"$lte": [{"$dayOfWeek": "$_id"}, 6]},
            ]},
        }},
        {"$lookup": {
            "from": "employees",
            "let": {"day": "$date"},
            "pipeline": [
                {"$match": {"department": department}},
                {"$match": {"$expr": {"$lte": ["$joined_on", "$$day"]}}},
                {"$count": "count"},
            ],
            "as": "headcount_result",
        }},
        {"$set": {
            "headcount": {"$ifNull": [{"$arrayElemAt": ["$headcount_result.count", 0]}, 0]},
            "present_count": {"$ifNull": ["$present_count", 0]},
            "late_count": {"$ifNull": ["$late_count", 0]},
        }},
        {"$set": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            {"$divide": [{"$floor": {"$add": [
                {"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 10000]},
                0.5,
            ]}}, 10000]},
            None,
        ]}}},
        {"$setWindowFields": {
            "sortBy": {"_id": 1},
            "output": {"moving_avg_raw": {
                "$avg": "$attendance_rate", "window": {"documents": [-6, 0]},
            }},
        }},
        {"$project": {
            "_id": 0, "date": 1, "is_working_day": 1, "headcount": 1,
            "present_count": 1, "late_count": 1, "attendance_rate": 1,
            "moving_avg_7d": {"$cond": [
                {"$eq": [{"$ifNull": ["$moving_avg_raw", None]}, None]},
                None,
                {"$divide": [{"$floor": {"$add": [
                    {"$multiply": ["$moving_avg_raw", 10000]}, 0.5,
                ]}}, 10000]},
            ]},
        }},
        {"$sort": {"date": 1}},
    ]
    items = list(db.employees.aggregate(pipeline))
    # A department may exist but have no employees joined by the requested end date.
    # Return the requested date spine in that case as well.
    if not items:
        cursor = from_date
        items = []
        while cursor <= to_date:
            working = cursor.weekday() < 5
            headcount = db.employees.count_documents({
                "department": department, "joined_on": {"$lte": cursor.isoformat()}
            })
            items.append({
                "date": cursor.isoformat(),
                "is_working_day": working,
                "headcount": headcount,
                "present_count": 0,
                "late_count": 0,
                "attendance_rate": None,
                "moving_avg_7d": None,
            })
            cursor += timedelta(days=1)
    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal[
        "attendance_list", "employee_monthly", "department_summary",
        "late_leaderboard", "department_trend",
    ],
    emp_code: str | None = None,
    month: str | None = None,
    department: str | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: date | None = None,
    date_to: date | None = None,
    status: Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"] | None = None,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    def require(value, name):
        if value is None:
            raise HTTPException(422, f"'{name}' is required")

    if endpoint == "attendance_list":
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from must be on or before date_to")
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if status:
            query["status"] = status
        if date_from or date_to:
            bounds = {}
            if date_from:
                bounds["$gte"] = date_from.isoformat()
            if date_to:
                bounds["$lte"] = date_to.isoformat()
            query["date"] = bounds
        collection = "attendance_logs"
        command = {
            "find": collection,
            "filter": query,
            "projection": {"_id": 0},
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }
    else:
        if endpoint in ("employee_monthly", "department_summary", "late_leaderboard"):
            require(month, "month")
            start_text, end_text = month_bounds(month)

        if endpoint == "employee_monthly":
            require(emp_code, "emp_code")
            collection = "employees"
            pipeline = [
                {"$match": {"emp_code": emp_code}},
                {"$lookup": {
                    "from": "attendance_logs",
                    "let": {"code": "$emp_code"},
                    "pipeline": [
                        {"$match": {"$expr": {"$and": [
                            {"$eq": ["$emp_code", "$$code"]},
                            {"$gte": ["$date", start_text]},
                            {"$lt": ["$date", end_text]},
                        ]}}},
                        {"$group": {"_id": None, "record_count": {"$sum": 1}}},
                    ],
                    "as": "stats",
                }},
            ]
        elif endpoint == "department_summary":
            collection = "employees"
            match = {"joined_on": {"$lt": end_text}}
            if department is not None:
                match["department"] = department
            pipeline = [
                {"$match": match},
                {"$lookup": {
                    "from": "attendance_logs",
                    "let": {"code": "$emp_code"},
                    "pipeline": [
                        {"$match": {"$expr": {"$and": [
                            {"$eq": ["$emp_code", "$$code"]},
                            {"$gte": ["$date", start_text]},
                            {"$lt": ["$date", end_text]},
                        ]}}},
                        {"$count": "record_count"},
                    ],
                    "as": "stats",
                }},
            ]
        elif endpoint == "late_leaderboard":
            collection = "attendance_logs"
            pipeline = [
                {"$match": {
                    "date": {"$gte": start_text, "$lt": end_text},
                    "late_minutes": {"$gt": 0},
                }},
                {"$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1},
                }},
                {"$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee",
                }},
            ]
            if department is not None:
                pipeline.append({"$match": {"employee.department": department}})
            pipeline.extend([
                {"$sort": {"total_late_minutes": -1, "_id": 1}},
                {"$limit": limit},
            ])
        else:
            require(department, "department")
            require(from_date, "from")
            require(to_date, "to")
            if to_date < from_date:
                raise HTTPException(422, "'to' must be on or after 'from'")
            if (to_date - from_date).days + 1 > 92:
                raise HTTPException(422, "Date range cannot exceed 92 days")
            collection = "employees"
            pipeline = [
                {"$match": {
                    "department": department,
                    "joined_on": {"$lte": to_date.isoformat()},
                }},
                {"$lookup": {
                    "from": "attendance_logs",
                    "let": {"code": "$emp_code"},
                    "pipeline": [
                        {"$match": {"$expr": {"$and": [
                            {"$eq": ["$emp_code", "$$code"]},
                            {"$gte": ["$date", from_date.isoformat()]},
                            {"$lte": ["$date", to_date.isoformat()]},
                        ]}}},
                    ],
                    "as": "logs",
                }},
            ]
        command = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}

    try:
        result = db.command("explain", command, verbosity="executionStats")
    except PyMongoError as exc:
        raise HTTPException(503, f"MongoDB explain failed: {exc.__class__.__name__}")
    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": make_json_safe(result),
    }
