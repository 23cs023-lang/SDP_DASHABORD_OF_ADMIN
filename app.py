# -*- coding: utf-8 -*-
# 1. Imports
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from flask import Flask, render_template, request, jsonify, make_response
from database.database import create_connection
from flask_cors import CORS
from utils.utils import generate_token, token_required, revoke_token
from datetime import datetime
from functools import wraps
import mysql.connector
import re

# 2. App setup
app = Flask(__name__)
app.secret_key = 'creovibe_sdp_project_2024'
CORS(app, supports_credentials=True, origins=["http://localhost:5000"])


def admin_required(f):
    """Ensure authenticated user is admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if getattr(request, 'username', None) != 'admin':
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Admin access required'}), 403
            from flask import redirect
            return redirect('/dashboard')
        return f(*args, **kwargs)
    return decorated


def _artist_uses_category_id(cursor):
    cursor.execute("SHOW COLUMNS FROM artist_table LIKE 'category_id'")
    return cursor.fetchone() is not None


def _resolve_artist_id_by_username(connection, username):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT Artist_ID
            FROM artist_table
            WHERE LOWER(Username) = LOWER(%s)
            LIMIT 1
            """,
            (username,)
        )
        row = cursor.fetchone()
        return row['Artist_ID'] if row else None
    finally:
        cursor.close()


def _resolve_category_id(connection, category_name):
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        "SELECT category_id FROM category_table WHERE LOWER(category_name) = LOWER(%s)",
        (category_name,)
    )
    row = cursor.fetchone()
    cursor.close()
    return row['category_id'] if row else None


def _normalize_verification_status(status_value):
    normalized = (status_value or '').strip().lower()
    aliases = {
        'approved': 'approved',
        'approve': 'approved',
        'verified': 'approved',
        'rejected': 'rejected',
        'reject': 'rejected',
        'pending': 'pending'
    }
    return aliases.get(normalized)


NAME_VALIDATION_MESSAGE = (
    "Name must contain only letters and be 3 to 20 characters long."
)
NAME_REGEX = re.compile(r"^[A-Za-z]{3,20}$")
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_REGEX = re.compile(r"^[0-9]{10}$")
PINCODE_REGEX = re.compile(r"^[0-9]{6}$")
EXPERIENCE_OPTIONS = {1, 2, 3, 4, 5}


def validate_name(name):
    if not NAME_REGEX.fullmatch(name or ""):
        return False
    if len(set((name or "").lower())) == 1:
        return False
    return True


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _city_table_has_pincode(cursor):
    cursor.execute("SHOW COLUMNS FROM city_table LIKE 'pincode'")
    return cursor.fetchone() is not None


def _artist_table_has_column(cursor, column_name):
    cursor.execute("SHOW COLUMNS FROM artist_table LIKE %s", (column_name,))
    return cursor.fetchone() is not None


def _table_has_column(cursor, table_name, column_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name} LIKE %s", (column_name,))
    return cursor.fetchone() is not None


def _table_exists(cursor, table_name):
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def _resolve_table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    rows = cursor.fetchall()
    columns = []
    for row in rows:
        if isinstance(row, dict):
            columns.append(row.get('Field'))
        else:
            columns.append(row[0])
    return [column for column in columns if column]


def _pick_column_name(available_columns, candidates, required=False):
    column_map = {column.lower(): column for column in available_columns}
    for candidate in candidates:
        matched = column_map.get(candidate.lower())
        if matched:
            return matched
    if required:
        raise ValueError(f"Required column missing. Tried: {', '.join(candidates)}")
    return None


def _subscription_plan_schema(cursor):
    if not _table_exists(cursor, 'subscription_plan_table'):
        raise ValueError("subscription_plan_table does not exist")

    columns = _resolve_table_columns(cursor, 'subscription_plan_table')
    schema = {
        'id': _pick_column_name(columns, ['Plan_ID', 'plan_id', 'id'], required=True),
        'name': _pick_column_name(columns, ['Plan_Name', 'plan_name', 'Plan_Type', 'plan_type', 'name'], required=True),
        'amount': _pick_column_name(columns, ['Amount', 'amount', 'Price', 'price', 'Plan_Amount', 'plan_amount'], required=True),
        'duration': _pick_column_name(columns, ['Duration_Days', 'duration_days', 'Duration', 'duration'], required=True),
        'features': _pick_column_name(columns, ['Description', 'description', 'Features', 'features']),
        'status': _pick_column_name(columns, ['Status', 'status', 'Is_Active', 'is_active', 'Active', 'active']),
        'created_at': _pick_column_name(columns, ['Created_At', 'created_at', 'CreatedDate', 'created_date', 'createdon', 'created_on'])
    }
    return schema


def _normalize_plan_status(status):
    value = (status or '').strip().lower()
    if value not in {'active', 'inactive'}:
        return None
    return value


def _status_value_for_db(status_column, normalized_status):
    if not status_column:
        return None
    if status_column.lower() in {'is_active', 'active'}:
        return 1 if normalized_status == 'active' else 0
    return normalized_status


def _validate_plan_payload(data):
    errors = {}

    plan_name = (data.get('plan_name') or '').strip()
    amount_raw = data.get('amount')
    duration_raw = data.get('duration_days')
    features = (data.get('features') or '').strip()
    status = _normalize_plan_status(data.get('status') or 'active')

    if not plan_name:
        errors['plan_name'] = 'Plan name is required'

    amount = None
    try:
        amount = float(amount_raw)
        if amount <= 0:
            errors['amount'] = 'Amount must be greater than 0'
    except (TypeError, ValueError):
        errors['amount'] = 'Amount must be a numeric value'

    duration_days = None
    try:
        duration_days = int(duration_raw)
        if duration_days <= 0:
            errors['duration_days'] = 'Duration must be greater than 0'
    except (TypeError, ValueError):
        errors['duration_days'] = 'Duration days must be a whole number'

    if not status:
        errors['status'] = 'Status must be Active or Inactive'

    return {
        'plan_name': plan_name,
        'amount': amount,
        'duration_days': duration_days,
        'features': features,
        'status': status
    }, errors


def _validate_artist_payload(connection, data, uses_category_id, artist_id=None):
    errors = {}

    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    gender = (data.get('gender') or '').strip()
    phone = (data.get('phone') or '').strip()
    email = (data.get('email') or '').strip().lower()
    experience_raw = data.get('experience_years')
    experience_years = _to_int(experience_raw)
    category_name = (data.get('category') or '').strip()
    dob_raw = (data.get('dob') or '').strip()
    pincode = (data.get('pincode') or '').strip()
    state_id = _to_int(data.get('state_id'))
    city_id = _to_int(data.get('city_id'))

    if not validate_name(first_name):
        errors['first_name'] = NAME_VALIDATION_MESSAGE
    if not validate_name(last_name):
        errors['last_name'] = NAME_VALIDATION_MESSAGE

    if not PHONE_REGEX.fullmatch(phone):
        errors['phone'] = 'Phone number must be exactly 10 digits.'

    if not EMAIL_REGEX.fullmatch(email):
        errors['email'] = 'Please enter a valid email address.'

    if experience_years not in EXPERIENCE_OPTIONS:
        errors['experience_years'] = 'Please select a valid experience.'

    dob_value = None
    if dob_raw:
        try:
            dob_value = datetime.strptime(dob_raw, '%Y-%m-%d').date()
            today = datetime.now().date()
            if dob_value > today:
                errors['dob'] = 'Date of Birth cannot be a future date.'
            else:
                age = today.year - dob_value.year - (
                    (today.month, today.day) < (dob_value.month, dob_value.day)
                )
                if age < 18:
                    errors['dob'] = 'Artist must be at least 18 years old.'
        except ValueError:
            errors['dob'] = 'Please enter a valid Date of Birth.'

    if not gender:
        errors['gender'] = 'Gender is required.'
    if not category_name:
        errors['category'] = 'Category is required.'
    if state_id is None or state_id <= 0:
        errors['state_id'] = 'Please select a valid state.'
    if city_id is None or city_id <= 0:
        errors['city_id'] = 'Please select a valid city.'
    if not PINCODE_REGEX.fullmatch(pincode):
        errors['pincode'] = 'Pincode must be exactly 6 digits.'

    if errors:
        return None, errors

    cursor = connection.cursor(dictionary=True)
    try:
        has_email_column = _artist_table_has_column(cursor, 'Email')
        has_experience_column = _artist_table_has_column(cursor, 'experience_years')
        if not has_email_column:
            errors['schema_email'] = "artist_table.Email column is required."
        if not has_experience_column:
            errors['schema_experience_years'] = "artist_table.experience_years column is required."
        if errors:
            return None, errors

        email_query = "SELECT Artist_ID FROM artist_table WHERE LOWER(Email) = LOWER(%s)"
        email_params = [email]
        if artist_id is not None:
            email_query += " AND Artist_ID <> %s"
            email_params.append(artist_id)
        cursor.execute(email_query, tuple(email_params))
        if cursor.fetchone():
            errors['email'] = 'Email already exists.'

        cursor.execute("SELECT state_id, state_name FROM state_table WHERE state_id = %s", (state_id,))
        if not cursor.fetchone():
            errors['state_id'] = 'Selected state does not exist.'

        if not _city_table_has_pincode(cursor):
            errors['schema'] = "city_table.pincode column is required."
            return None, errors

        cursor.execute("SELECT city_id, state_id, city_name, pincode FROM city_table WHERE city_id = %s", (city_id,))
        city_row = cursor.fetchone()
        if not city_row:
            errors['city_id'] = 'Selected city does not exist.'
        else:
            if int(city_row['state_id']) != state_id:
                errors['city_id'] = 'Selected city does not belong to selected state.'
            city_pincode = str(city_row['pincode']).strip() if city_row.get('pincode') is not None else ''
            if city_pincode != pincode:
                errors['pincode'] = 'Pincode does not match selected city.'

        category_id = None
        if uses_category_id and category_name:
            category_id = _resolve_category_id(connection, category_name)
            if not category_id:
                errors['category'] = 'Invalid category selected.'
    finally:
        cursor.close()

    if errors:
        return None, errors

    cleaned_data = {
        'first_name': first_name.title(),
        'last_name': last_name.title(),
        'gender': gender,
        'phone': phone,
        'email': email,
        'experience_years': experience_years,
        'dob': dob_value.isoformat() if dob_value else None,
        'category': category_name,
        'category_id': category_id,
        'pincode': pincode,
        'state_id': state_id,
        'city_id': city_id,
        'portfolio_path': (data.get('portfolio_path') or '').strip(),
        'status': data.get('status')
    }
    return cleaned_data, {}


def _insert_artist_record(connection, cleaned_data, uses_category_id):
    username = (
        f"{cleaned_data['first_name'].lower()}_{cleaned_data['last_name'].lower()}_"
        f"{datetime.now().strftime('%y%m%d%H%M%S')}"
    )
    cursor = connection.cursor()
    try:
        cursor.execute(
            """INSERT INTO artist_table
               (First_Name, Last_Name, Username, Password, Email, Gender, dob, Phone_Number,
                State_ID, City_ID, category_id, Portfolio_Path, profile_pic, experience_years,
                price_per_hour, rating, verification_status, is_enabled)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                cleaned_data['first_name'],
                cleaned_data['last_name'],
                username,
                'default123',
                cleaned_data['email'],
                cleaned_data['gender'],
                cleaned_data['dob'],
                cleaned_data['phone'],
                cleaned_data['state_id'],
                cleaned_data['city_id'],
                cleaned_data['category_id'] if uses_category_id else None,
                cleaned_data['portfolio_path'],
                '',
                cleaned_data['experience_years'],
                0,
                0,
                'pending',
                1 if cleaned_data.get('status') != 'Inactive' else 0
            )
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        cursor.close()


def _create_announcement_notification(connection, title, message):
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute("SHOW TABLES LIKE 'notification_table'")
        if not cursor.fetchone():
            return False

        cursor.execute("SHOW COLUMNS FROM notification_table")
        available_columns = {row[0].lower() for row in cursor.fetchall()}

        payload = {}
        if 'type' in available_columns:
            payload['type'] = 'announcement'
        if 'title' in available_columns:
            payload['title'] = title
        if 'message' in available_columns:
            payload['message'] = message
        if 'icon' in available_columns:
            payload['icon'] = 'bullhorn'
        if 'priority' in available_columns:
            payload['priority'] = 'medium'
        if 'is_read' in available_columns:
            payload['is_read'] = 0
        if 'created_at' in available_columns:
            payload['created_at'] = datetime.now()

        if not payload:
            return False

        columns = list(payload.keys())
        values = [payload[column] for column in columns]
        placeholders = ', '.join(['%s'] * len(columns))
        cursor.execute(
            f"INSERT INTO notification_table ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values)
        )
        return True
    except Exception as notification_error:
        print(f"NOTIFICATION INSERT SKIPPED: {notification_error}")
        return False
    finally:
        if cursor:
            cursor.close()


@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    if request.path.startswith('/api/'):
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        origin = request.headers.get('Origin')
        if origin:
            response.headers['Access-Control-Allow-Origin'] = origin
    return response


@app.route('/api/artists', methods=['OPTIONS'])
@app.route('/api/artists/<int:artist_id>', methods=['OPTIONS'])
@app.route('/api/clients', methods=['OPTIONS'])
@app.route('/api/clients/<int:client_id>', methods=['OPTIONS'])
@app.route('/api/auth/login', methods=['OPTIONS'])
def handle_options(artist_id=None, client_id=None):
    response = jsonify({})
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response


# ========== AUTHENTICATION ROUTES ==========

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    elif request.method == 'POST':
        data = request.json if request.is_json else request.form
        username = data.get('username')
        password = data.get('password')
        if username == 'admin' and password == 'admin123':
            token = generate_token(username)
            response_data = {
                'success': True,
                'message': 'Login successful',
                'token': token,
                'user': {'username': username, 'role': 'admin'}
            }
            response = jsonify(response_data)
            response.set_cookie(
                'admin_token', token,
                httponly=True, secure=False, samesite='Lax', max_age=24*60*60
            )
            return response
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401


@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not password:
        return jsonify({'success': False, 'error': 'username and password are required'}), 400

    if username == 'admin' and password == 'admin123':
        token = generate_token(username)
        return jsonify({
            'success': True, 'message': 'Login successful',
            'token': token, 'user': {'username': username, 'role': 'admin'}
        })

    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500

    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT Artist_ID, Username, verification_status, is_enabled
            FROM artist_table
            WHERE LOWER(Username) = LOWER(%s) AND Password = %s
            LIMIT 1
            """,
            (username, password)
        )
        artist = cursor.fetchone()
        if not artist:
            return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
        if int(artist.get('is_enabled') or 0) != 1:
            return jsonify({'success': False, 'error': 'Artist account is disabled'}), 403
        token = generate_token(artist['Username'])
        return jsonify({
            'success': True, 'message': 'Login successful',
            'token': token,
            'user': {
                'artist_id': artist['Artist_ID'],
                'username': artist['Username'],
                'role': 'artist',
                'verification_status': artist.get('verification_status')
            }
        })
    except Exception as e:
        print(f"ARTIST LOGIN ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        connection.close()


@app.route('/logout')
def logout():
    token = request.cookies.get('admin_token')
    if token:
        revoke_token(token)
    from flask import redirect
    response = redirect('/login')
    response.delete_cookie('admin_token')
    return response


@app.route('/api/auth/verify')
def verify_auth():
    token = request.cookies.get('admin_token')
    if token:
        from utils.utils import verify_token
        username = verify_token(token)
        if username:
            return jsonify({'authenticated': True, 'username': username})
    return jsonify({'authenticated': False}), 401


# ========== PROTECTED ROUTES ==========

@app.route('/')
def home():
    from flask import redirect
    return redirect('/dashboard')


@app.route('/dashboard')
@token_required
@admin_required
def dashboard():
    username = request.username
    connection = create_connection()
    stats = {
        'total_artists': 0, 'active_bookings': 0,
        'pending_requests': 0, 'total_revenue': '0.00', 'db_status': 'Disconnected'
    }
    recent_bookings = []
    recent_activity = []

    if connection:
        try:
            cursor = connection.cursor(dictionary=True)

            cursor.execute("""
                SELECT COUNT(*) AS count FROM artist_table
                WHERE LOWER(COALESCE(verification_status, '')) = 'approved'
                AND COALESCE(is_enabled, 0) = 1
            """)
            stats['total_artists'] = cursor.fetchone()['count']

            cursor.execute("""
                SELECT COUNT(*) AS count FROM booking_table
                WHERE LOWER(COALESCE(Booking_Status, '')) IN ('pending', 'confirmed')
            """)
            stats['active_bookings'] = cursor.fetchone()['count']

            cursor.execute("""
                SELECT COUNT(*) AS count FROM artist_table
                WHERE LOWER(COALESCE(verification_status, '')) = 'pending'
            """)
            stats['pending_requests'] = cursor.fetchone()['count']

            cursor.execute("""
                SELECT COALESCE(SUM(Amount), 0) AS total FROM payment_table
                WHERE LOWER(COALESCE(Payment_Status, '')) IN ('success', 'completed')
            """)
            result = cursor.fetchone()
            total_revenue = float(result['total']) if result and result['total'] is not None else 0.0
            stats['total_revenue'] = f"{total_revenue:,.2f}"
            stats['db_status'] = "Connected"

            cursor.execute("""
                SELECT
                    b.Booking_ID AS id,
                    CONCAT(c.first_name, ' ', c.last_name) AS client,
                    CONCAT(a.First_Name, ' ', a.Last_Name) AS artist,
                    b.Booking_Status AS status,
                    DATE_FORMAT(b.Booked_At, '%d %b %Y') AS date,
                    COALESCE(pay.total_amount, 0) AS amount
                FROM booking_table b
                LEFT JOIN client_table c ON b.Client_ID = c.client_id
                LEFT JOIN artist_table a ON b.Artist_ID = a.Artist_ID
                LEFT JOIN (
                    SELECT Booking_ID,
                        SUM(CASE WHEN LOWER(COALESCE(Payment_Status, '')) IN ('success', 'completed') THEN Amount ELSE 0 END) AS total_amount
                    FROM payment_table GROUP BY Booking_ID
                ) pay ON b.Booking_ID = pay.Booking_ID
                ORDER BY b.Booked_At DESC LIMIT 5
            """)
            recent_bookings = cursor.fetchall()

            for booking in recent_bookings:
                booking['status'] = (booking.get('status') or 'pending').lower()
                booking['event'] = 'Booking'
                booking['amount'] = f"\u20B9{float(booking.get('amount') or 0):,.2f}"

            cursor.execute("""
                SELECT CONCAT(First_Name, ' ', Last_Name, ' joined as new artist') AS activity
                FROM artist_table ORDER BY created_at DESC LIMIT 1
            """)
            latest_artist = cursor.fetchone()
            if latest_artist:
                recent_activity.append(latest_artist['activity'])

            cursor.execute("""
                SELECT CONCAT(c.first_name, ' ', c.last_name, ' booked ', a.First_Name, ' ', a.Last_Name) AS activity
                FROM booking_table b
                JOIN client_table c ON b.Client_ID = c.client_id
                JOIN artist_table a ON b.Artist_ID = a.Artist_ID
                ORDER BY b.Booked_At DESC LIMIT 1
            """)
            latest_booking = cursor.fetchone()
            if latest_booking:
                recent_activity.append(latest_booking['activity'])

            if not recent_activity:
                recent_activity = ['No recent activity', 'System is running', 'Database connected']

            cursor.close()
            connection.close()
        except Exception as e:
            print(f"Dashboard error: {e}")
            stats['db_status'] = "Error"

    return render_template('dashboard.html', stats=stats, bookings=recent_bookings,
                           activities=recent_activity, username=username)


# ========== ARTISTS ROUTES ==========

@app.route('/artists')
@token_required
@admin_required
def artists():
    username = request.username
    connection = create_connection()
    artists_list = []

    if connection:
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT
                    a.Artist_ID as id,
                    CONCAT(a.First_Name, ' ', a.Last_Name) as name,
                    a.First_Name, a.Last_Name,
                    COALESCE(cat.category_name, 'Unknown') as category,
                    CASE WHEN a.is_enabled = 1 THEN 'Active' ELSE 'Inactive' END as status,
                    a.Gender,
                    a.Phone_Number as phone,
                    a.Email as email,
                    '' as pincode,
                    s.state_name as location,
                    a.Portfolio_Path as description,
                    a.verification_status,
                    a.created_at
                FROM artist_table a
                LEFT JOIN state_table s ON a.State_ID = s.state_id
                LEFT JOIN category_table cat ON a.category_id = cat.category_id
                ORDER BY a.created_at DESC
            """)
            artists_list = cursor.fetchall()
            cursor.close()
            connection.close()
        except Exception as e:
            print(f"ARTISTS DATABASE ERROR: {e}")
            artists_list = []

    return render_template('artists.html', artists=artists_list, username=username)


# ========== CLIENTS ROUTES ==========

@app.route('/clients')
@token_required
@admin_required
def clients():
    username = request.username
    connection = create_connection()
    clients_list = []

    if connection:
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT 
                    client_id as id,
                    CONCAT(first_name, ' ', last_name) as name,
                    first_name,
                    last_name,
                    username,
                    gender,
                    dob,
                    phone_number as phone,
                    state_id,
                    city_id,
                    is_enabled,
                    is_admin,
                    created_at,
                    CONCAT(first_name, '.', last_name, '@example.com') as email
                FROM client_table
                ORDER BY created_at DESC
            """)
            clients_list = cursor.fetchall()
            cursor.close()
            connection.close()
        except Exception as e:
            print(f"CLIENTS DATABASE ERROR: {e}")
            clients_list = []

    return render_template('clients.html', clients=clients_list, username=username)


# ========== BOOKINGS ROUTES ==========

@app.route('/bookings')
@token_required
@admin_required
def bookings():
    username = request.username
    connection = create_connection()
    bookings_list = []

    if connection:
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT
                    b.Booking_ID as id,
                    CONCAT(c.First_Name, ' ', c.Last_Name) as client,
                    CONCAT(a.First_Name, ' ', a.Last_Name) as artist,
                    b.Booking_Status as status,
                    DATE_FORMAT(b.Booked_At, '%d %b %Y') as date,
                    COALESCE(p.Amount, 0) as amount,
                    p.Payment_Status as payment_status,
                    b.Slot_ID as slot_id
                FROM booking_table b
                LEFT JOIN client_table c ON b.Client_ID = c.Client_ID
                LEFT JOIN artist_table a ON b.Artist_ID = a.Artist_ID
                LEFT JOIN payment_table p ON b.Booking_ID = p.Booking_ID
                ORDER BY b.Booked_At DESC
            """)
            bookings_list = cursor.fetchall()
            for booking in bookings_list:
                if booking['amount']:
                    booking['amount'] = f"\u20B9{float(booking['amount']):,.2f}"
                else:
                    booking['amount'] = '\u20B9 -'
                booking['event'] = 'Booking'
            cursor.close()
            connection.close()
        except Exception as e:
            print(f"BOOKINGS DATABASE ERROR: {e}")
            bookings_list = []

    return render_template('bookings.html', bookings=bookings_list, username=username)


@app.route('/api/bookings/<int:booking_id>', methods=['GET'])
@token_required
@admin_required
def get_booking(booking_id):
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT 
                b.Booking_ID, b.Booking_Status, b.Booked_At,
                b.cancellation_reason, b.cancelled_at,
                CONCAT(c.first_name, ' ', c.last_name) as client_name,
                c.phone_number as client_phone,
                CONCAT(a.First_Name, ' ', a.Last_Name) as artist_name,
                COALESCE(cat.category_name, 'Unknown') as category,
                cal.Slot_Date, cal.Start_Time, cal.End_Time, cal.Slot_type,
                COALESCE(p.amount, 0) as amount,
                p.payment_status, p.payment_method, p.transaction_id
            FROM booking_table b
            LEFT JOIN client_table c ON b.Client_ID = c.client_id
            LEFT JOIN artist_table a ON b.Artist_ID = a.Artist_ID
            LEFT JOIN category_table cat ON a.category_id = cat.category_id
            LEFT JOIN calendar_table cal ON b.Slot_ID = cal.Slot_ID
            LEFT JOIN payment_table p ON b.Booking_ID = p.booking_id
            WHERE b.Booking_ID = %s
        """, (booking_id,))
        booking = cursor.fetchone()
        if not booking:
            return jsonify({'success': False, 'error': 'Booking not found'}), 404
            
        # Convert timedelta/datetime objects to strings for JSON serialization
        for key, value in booking.items():
            if hasattr(value, 'isoformat'):
                booking[key] = value.isoformat()
            elif hasattr(value, 'total_seconds'): # timedelta
                booking[key] = str(value)
                
        return jsonify({'success': True, 'booking': booking})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()


@app.route('/api/bookings/<int:booking_id>', methods=['PUT'])
@token_required
@admin_required
def update_booking(booking_id):
    data = request.json or {}
    new_status = (data.get('status') or '').strip().lower()
    if new_status not in ['pending', 'confirmed', 'cancelled', 'completed']:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE booking_table SET Booking_Status = %s WHERE Booking_ID = %s",
            (new_status, booking_id)
        )
        connection.commit()
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'error': 'Booking not found'}), 404
        return jsonify({'success': True, 'message': f'Booking status updated to {new_status}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()


@app.route('/api/bookings/<int:booking_id>/cancel', methods=['POST'])
@token_required
@admin_required
def cancel_booking(booking_id):
    data = request.json or {}
    reason = (data.get('reason') or 'Cancelled by admin').strip()
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE booking_table 
            SET Booking_Status = 'cancelled',
                cancellation_reason = %s,
                cancelled_at = NOW(),
                cancelled_by = 'admin'
            WHERE Booking_ID = %s
        """, (reason, booking_id))
        connection.commit()
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'error': 'Booking not found'}), 404
        return jsonify({'success': True, 'message': 'Booking cancelled successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()


# ========== NOTIFICATION ROUTE ==========

@app.route('/notifications')
@token_required
@admin_required
def notifications():
    username = request.username
    connection = create_connection()
    notification_list = []

    if connection:
        try:
            cursor = connection.cursor(dictionary=True)

            cursor.execute("""
                SELECT 'verification' as type, 'New Artist Registration' as title,
                    CONCAT(First_Name, ' ', Last_Name, ' has registered and needs verification') as message,
                    created_at as date, 'user-check' as icon, 'high' as priority, Artist_ID as reference_id
                FROM artist_table WHERE verification_status = 'pending' ORDER BY created_at DESC
            """)
            verification_notifications = cursor.fetchall()

            cursor.execute("""
                SELECT 'booking' as type, 'New Booking' as title,
                    CONCAT(c.first_name, ' ', c.last_name, ' booked ', a.First_Name, ' ', a.Last_Name) as message,
                    b.Booked_At as date, 'calendar-check' as icon, 'medium' as priority, b.Booking_ID as reference_id
                FROM booking_table b
                JOIN client_table c ON b.Client_ID = c.client_id
                JOIN artist_table a ON b.Artist_ID = a.Artist_ID
                ORDER BY b.Booked_At DESC LIMIT 5
            """)
            booking_notifications = cursor.fetchall()

            cursor.execute("""
                SELECT 'payment' as type, 'Payment Received' as title,
                    CONCAT('Payment of Rs.', Amount, ' received for booking #', Booking_ID) as message,
                    Paid_at as date, 'rupee-sign' as icon, 'medium' as priority, Payment_ID as reference_id
                FROM payment_table WHERE Payment_Status = 'success' ORDER BY Paid_at DESC LIMIT 5
            """)
            payment_notifications = cursor.fetchall()

            cursor.execute("""
                SELECT 'announcement' as type, 'New Category Available' as title,
                    CONCAT('New artist category ', category_name, ' is now available') as message,
                    created_at as date, 'bullhorn' as icon, 'low' as priority, category_id as reference_id
                FROM category_table ORDER BY created_at DESC LIMIT 5
            """)
            category_notifications = cursor.fetchall()

            notification_list = (
                verification_notifications + booking_notifications +
                payment_notifications + category_notifications
            )
            notification_list.sort(key=lambda x: x.get('date', datetime.now()), reverse=True)
            cursor.close()
            connection.close()
        except Exception as e:
            print(f"NOTIFICATIONS DATABASE ERROR: {e}")
            notification_list = []

    return render_template('notifications.html', notifications=notification_list,
                           username=username, notification_count=len(notification_list))


# ========== FEEDBACK ROUTE ==========

@app.route('/feedback')
@token_required
@admin_required
def feedback():
    username = request.username
    connection = create_connection()
    feedback_list = []
    feedback_error = None

    if connection:
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT
                    f.feedback_id AS Feedback_ID,
                    CONCAT(c.first_name, ' ', c.last_name) AS Client_Name,
                    CONCAT(a.First_Name, ' ', a.Last_Name) AS Artist_Name,
                    f.rating AS Rating, f.comments AS Comments,
                    cal.Slot_Date AS Slot_Date, cal.Start_Time AS Start_Time,
                    cal.End_Time AS End_Time, b.Booking_Status AS Booking_Status,
                    f.created_at AS Feedback_Created_Date
                FROM feedback_table f
                INNER JOIN booking_table b ON f.booking_id = b.Booking_ID
                INNER JOIN client_table c ON b.Client_ID = c.client_id
                INNER JOIN artist_table a ON b.Artist_ID = a.Artist_ID
                INNER JOIN calendar_table cal ON b.Slot_ID = cal.Slot_ID
                ORDER BY f.created_at DESC
            """)
            feedback_list = cursor.fetchall()
            cursor.close()
            connection.close()
        except Exception as e:
            print(f"FEEDBACK DATABASE ERROR: {e}")
            feedback_list = []
            feedback_error = str(e)
    else:
        feedback_error = "Database connection failed"

    response = make_response(render_template(
        'feedback.html', feedbacks=feedback_list, username=username,
        feedback_count=len(feedback_list), feedback_error=feedback_error
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@app.route('/api/admin/feedback/<int:feedback_id>', methods=['DELETE'])
@token_required
@admin_required
def admin_delete_feedback(feedback_id):
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM feedback_table WHERE feedback_id = %s", (feedback_id,))
        connection.commit()
        affected = cursor.rowcount
        cursor.close()
        connection.close()
        if affected == 0:
            return jsonify({'success': False, 'error': 'Feedback not found'}), 404
        return jsonify({'success': True, 'message': 'Feedback removed successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== CATEGORY MANAGEMENT ROUTES ==========

@app.route('/admin/categories')
@token_required
@admin_required
def admin_categories():
    return render_template('categories.html', username=request.username)


@app.route('/api/admin/categories', methods=['GET', 'POST'])
@token_required
@admin_required
def admin_categories_api():
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        if request.method == 'GET':
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT category_id, category_name, created_at
                FROM category_table ORDER BY created_at DESC, category_id DESC
            """)
            categories = cursor.fetchall()
            cursor.close()
            connection.close()
            return jsonify({'success': True, 'categories': categories})

        data = request.json or {}
        category_name = (data.get('category_name') or '').strip()
        if not category_name:
            return jsonify({'success': False, 'error': 'Category name is required'}), 400

        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT category_id FROM category_table WHERE LOWER(category_name) = LOWER(%s)",
            (category_name,)
        )
        if cursor.fetchone():
            cursor.close()
            connection.close()
            return jsonify({'success': False, 'error': 'Category name already exists'}), 409

        cursor.execute("INSERT INTO category_table (category_name) VALUES (%s)", (category_name,))
        category_id = cursor.lastrowid
        connection.commit()
        _create_announcement_notification(connection, "New Category Added", f"Category '{category_name}' has been added.")
        connection.commit()
        cursor.execute(
            "SELECT category_id, category_name, created_at FROM category_table WHERE category_id = %s",
            (category_id,)
        )
        created = cursor.fetchone()
        cursor.close()
        connection.close()
        return jsonify({'success': True, 'message': f"Category '{category_name}' added successfully", 'category': created})
    except mysql.connector.Error as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/categories/<int:category_id>', methods=['PUT', 'DELETE'])
@token_required
@admin_required
def admin_category_item_api(category_id):
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT category_id, category_name FROM category_table WHERE category_id = %s", (category_id,))
        category = cursor.fetchone()
        if not category:
            cursor.close()
            connection.close()
            return jsonify({'success': False, 'error': 'Category not found'}), 404

        if request.method == 'PUT':
            data = request.json or {}
            category_name = (data.get('category_name') or '').strip()
            if not category_name:
                cursor.close()
                connection.close()
                return jsonify({'success': False, 'error': 'Category name is required'}), 400
            cursor.execute(
                "SELECT category_id FROM category_table WHERE LOWER(category_name) = LOWER(%s) AND category_id <> %s",
                (category_name, category_id)
            )
            if cursor.fetchone():
                cursor.close()
                connection.close()
                return jsonify({'success': False, 'error': 'Category name already exists'}), 409
            cursor.execute("UPDATE category_table SET category_name = %s WHERE category_id = %s", (category_name, category_id))
            connection.commit()
            cursor.execute("SELECT category_id, category_name, created_at FROM category_table WHERE category_id = %s", (category_id,))
            updated = cursor.fetchone()
            cursor.close()
            connection.close()
            return jsonify({'success': True, 'message': 'Category updated successfully', 'category': updated})

        cursor.execute("SELECT COUNT(*) as count FROM artist_table WHERE category_id = %s", (category_id,))
        result = cursor.fetchone()
        if result and result['count'] > 0:
            cursor.close()
            connection.close()
            return jsonify({'success': False, 'error': 'Cannot delete: category is assigned to artists'}), 409

        try:
            cursor.execute("DELETE FROM category_table WHERE category_id = %s", (category_id,))
            connection.commit()
        except mysql.connector.Error as e:
            if e.errno == 1451:
                cursor.close()
                connection.close()
                return jsonify({'success': False, 'error': 'Cannot delete: category is referenced by other records'}), 409
            raise
        cursor.close()
        connection.close()
        return jsonify({'success': True, 'message': 'Category deleted successfully'})
    except mysql.connector.Error as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== SUBSCRIPTION PLAN MANAGEMENT ROUTES ==========

@app.route('/admin/subscription-plans')
@token_required
@admin_required
def admin_subscription_plans():
    plans = []
    plans_error = None
    success_message = (request.args.get('success') or '').strip()

    connection = create_connection()
    if not connection:
        return render_template('subscription_plans.html', username=request.username,
                               plans=[], plans_count=0, plans_error='Database connection failed',
                               success_message=success_message)

    cursor = connection.cursor(dictionary=True)
    try:
        schema = _subscription_plan_schema(cursor)
        status_expr = "'active'"
        status_col = schema.get('status')
        if status_col:
            if status_col.lower() in {'is_active', 'active'}:
                status_expr = f"CASE WHEN COALESCE(sp.`{status_col}`, 0) = 1 THEN 'active' ELSE 'inactive' END"
            else:
                status_expr = f"LOWER(COALESCE(sp.`{status_col}`, 'inactive'))"

        features_expr = "''" if not schema.get('features') else f"COALESCE(sp.`{schema['features']}`, '')"
        created_expr = "NULL" if not schema.get('created_at') else f"DATE_FORMAT(sp.`{schema['created_at']}`, '%Y-%m-%d %H:%i:%s')"
        amount_expr = f"(COALESCE(sp.`{schema['amount']}`, 0) + 0)"
        order_expr = f"sp.`{schema['id']}` DESC"
        if schema.get('created_at'):
            order_expr = f"sp.`{schema['created_at']}` DESC, sp.`{schema['id']}` DESC"

        query = f"""
            SELECT sp.`{schema['id']}` AS Plan_ID, sp.`{schema['name']}` AS Plan_Name,
                {amount_expr} AS Amount, sp.`{schema['duration']}` AS Duration_Days,
                {features_expr} AS Features, {status_expr} AS Status, {created_expr} AS Created_At
            FROM subscription_plan_table sp ORDER BY {order_expr}
        """
        cursor.execute(query)
        plans = cursor.fetchall()
    except Exception as e:
        print(f"SUBSCRIPTION PLANS LOAD ERROR: {e}")
        plans_error = str(e)
        plans = []
    finally:
        cursor.close()
        connection.close()

    return render_template('subscription_plans.html', username=request.username,
                           plans=plans, plans_count=len(plans),
                           plans_error=plans_error, success_message=success_message)


@app.route('/api/admin/subscription-plans', methods=['GET', 'POST'])
@token_required
@admin_required
def admin_subscription_plans_api():
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500

    cursor = connection.cursor(dictionary=True)
    try:
        schema = _subscription_plan_schema(cursor)

        if request.method == 'GET':
            status_expr = "'active'"
            status_col = schema.get('status')
            if status_col:
                if status_col.lower() in {'is_active', 'active'}:
                    status_expr = f"CASE WHEN COALESCE(sp.`{status_col}`, 0) = 1 THEN 'active' ELSE 'inactive' END"
                else:
                    status_expr = f"LOWER(COALESCE(sp.`{status_col}`, 'inactive'))"
            features_expr = "''" if not schema.get('features') else f"COALESCE(sp.`{schema['features']}`, '')"
            created_expr = "NULL" if not schema.get('created_at') else f"DATE_FORMAT(sp.`{schema['created_at']}`, '%Y-%m-%d %H:%i:%s')"
            amount_expr = f"(COALESCE(sp.`{schema['amount']}`, 0) + 0)"
            order_expr = f"sp.`{schema['id']}` DESC"
            if schema.get('created_at'):
                order_expr = f"sp.`{schema['created_at']}` DESC, sp.`{schema['id']}` DESC"

            cursor.execute(f"""
                SELECT sp.`{schema['id']}` AS Plan_ID, sp.`{schema['name']}` AS Plan_Name,
                    {amount_expr} AS Amount, sp.`{schema['duration']}` AS Duration_Days,
                    {features_expr} AS Features, {status_expr} AS Status, {created_expr} AS Created_At
                FROM subscription_plan_table sp ORDER BY {order_expr}
            """)
            plans = cursor.fetchall()
            return jsonify({'success': True, 'plans': plans})

        payload, errors = _validate_plan_payload(request.json or {})
        if errors:
            return jsonify({'success': False, 'errors': errors, 'error': 'Validation failed'}), 400

        cursor.execute(
            f"SELECT `{schema['id']}` AS plan_id FROM subscription_plan_table WHERE LOWER(`{schema['name']}`) = LOWER(%s) LIMIT 1",
            (payload['plan_name'],)
        )
        if cursor.fetchone():
            return jsonify({'success': False, 'error': 'Plan name already exists'}), 409

        insert_columns = [schema['name'], schema['amount'], schema['duration']]
        insert_values = [payload['plan_name'], payload['amount'], payload['duration_days']]
        placeholders = ['%s', '%s', '%s']

        if schema.get('features'):
            insert_columns.append(schema['features'])
            insert_values.append(payload['features'])
            placeholders.append('%s')
        if schema.get('status'):
            insert_columns.append(schema['status'])
            insert_values.append(_status_value_for_db(schema['status'], payload['status']))
            placeholders.append('%s')

        query = (
            f"INSERT INTO subscription_plan_table ({', '.join([f'`{c}`' for c in insert_columns])}) "
            f"VALUES ({', '.join(placeholders)})"
        )
        cursor.execute(query, tuple(insert_values))
        connection.commit()
        return jsonify({'success': True, 'message': 'Subscription plan created successfully', 'plan_id': cursor.lastrowid})
    except Exception as e:
        print(f"SUBSCRIPTION PLAN API ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        connection.close()


@app.route('/api/admin/subscription-plans/<int:plan_id>', methods=['PUT'])
@token_required
@admin_required
def admin_subscription_plan_update(plan_id):
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        schema = _subscription_plan_schema(cursor)
        payload, errors = _validate_plan_payload(request.json or {})
        if errors:
            return jsonify({'success': False, 'errors': errors, 'error': 'Validation failed'}), 400

        cursor.execute(f"SELECT `{schema['id']}` AS plan_id FROM subscription_plan_table WHERE `{schema['id']}` = %s", (plan_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'error': 'Plan not found'}), 404

        cursor.execute(
            f"SELECT `{schema['id']}` AS plan_id FROM subscription_plan_table WHERE LOWER(`{schema['name']}`) = LOWER(%s) AND `{schema['id']}` <> %s LIMIT 1",
            (payload['plan_name'], plan_id)
        )
        if cursor.fetchone():
            return jsonify({'success': False, 'error': 'Plan name already exists'}), 409

        update_columns = [
            (schema['name'], payload['plan_name']),
            (schema['amount'], payload['amount']),
            (schema['duration'], payload['duration_days'])
        ]
        if schema.get('features'):
            update_columns.append((schema['features'], payload['features']))
        if schema.get('status'):
            update_columns.append((schema['status'], _status_value_for_db(schema['status'], payload['status'])))

        set_clause = ', '.join([f"`{col}` = %s" for col, _ in update_columns])
        params = [val for _, val in update_columns] + [plan_id]
        cursor.execute(f"UPDATE subscription_plan_table SET {set_clause} WHERE `{schema['id']}` = %s", tuple(params))
        connection.commit()
        return jsonify({'success': True, 'message': 'Plan updated successfully.'})
    except Exception as e:
        print(f"SUBSCRIPTION PLAN UPDATE ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        connection.close()


@app.route('/api/admin/subscription-plans/<int:plan_id>/deactivate', methods=['POST'])
@token_required
@admin_required
def admin_subscription_plan_deactivate(plan_id):
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        schema = _subscription_plan_schema(cursor)
        status_col = schema.get('status')
        if not status_col:
            return jsonify({'success': False, 'error': 'Status column not available'}), 400
        cursor.execute(f"SELECT `{schema['id']}` AS plan_id FROM subscription_plan_table WHERE `{schema['id']}` = %s", (plan_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'error': 'Plan not found'}), 404
        cursor.execute(
            f"UPDATE subscription_plan_table SET `{status_col}` = %s WHERE `{schema['id']}` = %s",
            (_status_value_for_db(status_col, 'inactive'), plan_id)
        )
        connection.commit()
        return jsonify({'success': True, 'message': 'Plan deactivated successfully.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        connection.close()


@app.route('/admin/subscription-plans/<int:plan_id>/notify', methods=['POST'])
@token_required
@admin_required
def admin_subscription_plan_notify(plan_id):
    from flask import redirect
    connection = create_connection()
    if not connection:
        return redirect('/admin/subscription-plans?success=Unable+to+send+notification')
    cursor = connection.cursor(dictionary=True)
    try:
        schema = _subscription_plan_schema(cursor)
        cursor.execute(f"SELECT `{schema['id']}` AS plan_id FROM subscription_plan_table WHERE `{schema['id']}` = %s", (plan_id,))
        if not cursor.fetchone():
            return redirect('/admin/subscription-plans?success=Plan+not+found')
        created = _create_announcement_notification(connection, "Subscription Plans Updated",
                                                    "New or updated subscription plans are now available.")
        connection.commit()
        if not created:
            return redirect('/admin/subscription-plans?success=Notification+table+not+available')
        return redirect('/admin/subscription-plans?success=Artists+notified+successfully')
    except Exception as e:
        print(f"SUBSCRIPTION PLAN NOTIFY ERROR: {e}")
        return redirect('/admin/subscription-plans?success=Unable+to+send+notification')
    finally:
        cursor.close()
        connection.close()


# ========== PAYMENTS ROUTE ==========

@app.route('/admin/payments')
@token_required
def admin_payments():
    username = request.username
    payments_list = []
    payments_error = None

    connection = create_connection()
    if connection:
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT
                    p.Payment_ID, p.Booking_ID, p.Amount, p.Payment_Status, p.Paid_at,
                    CONCAT(c.first_name, ' ', c.last_name) as client_name,
                    CONCAT(a.First_Name, ' ', a.Last_Name) as artist_name
                FROM payment_table p
                LEFT JOIN booking_table b ON p.Booking_ID = b.Booking_ID
                LEFT JOIN client_table c ON b.Client_ID = c.client_id
                LEFT JOIN artist_table a ON b.Artist_ID = a.Artist_ID
                ORDER BY p.Paid_at DESC
            """)
            payments_list = cursor.fetchall()
            cursor.close()
            connection.close()
        except Exception as e:
            print(f"PAYMENTS DATABASE ERROR: {e}")
            payments_error = str(e)
    else:
        payments_error = "Database connection failed"

    return render_template('payments.html', payments=payments_list,
                           payments_count=len(payments_list),
                           payments_error=payments_error, username=username)


def _fetch_receipt_row(connection, payment_id):
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                p.payment_id AS Payment_ID,
                p.booking_id AS Booking_ID,
                p.amount AS Amount,
                p.payment_status AS Payment_Status,
                p.payment_method AS payment_method,
                p.transaction_id AS transaction_id,
                p.paid_at AS Paid_at,
                p.created_at AS Created_At,
                CONCAT(c.first_name, ' ', c.last_name) AS client_name,
                CONCAT(a.First_Name, ' ', a.Last_Name) AS artist_name
            FROM payment_table p
            LEFT JOIN booking_table b ON p.booking_id = b.Booking_ID
            LEFT JOIN client_table c ON b.Client_ID = c.client_id
            LEFT JOIN artist_table a ON b.Artist_ID = a.Artist_ID
            WHERE p.payment_id = %s
            LIMIT 1
        """, (payment_id,))
        return cursor.fetchone()
    finally:
        cursor.close()


@app.route('/admin/payments/receipt/<int:payment_id>')
@token_required
def payment_receipt(payment_id):
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        receipt = _fetch_receipt_row(connection, payment_id)
        if not receipt:
            return jsonify({'success': False, 'error': 'Receipt not found'}), 404
        return render_template('payment_receipt.html', payment=receipt, username=request.username)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()


@app.route('/admin/payments/receipt/<int:payment_id>/download')
@token_required
def download_payment_receipt(payment_id):
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        receipt = _fetch_receipt_row(connection, payment_id)
        if not receipt:
            return jsonify({'success': False, 'error': 'Receipt not found'}), 404

        html = render_template('payment_receipt.html', username=request.username, payment=receipt, is_download=True)
        response = make_response(html)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        response.headers['Content-Disposition'] = f"attachment; filename=receipt_{payment_id}.html"
        return response
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()


# ========== ADMIN CALENDAR ROUTES ==========

@app.route('/admin/calendar')
@token_required
@admin_required
def admin_calendar():
    return render_template('admin_calendar.html', username=request.username)


@app.route('/api/admin/calendar/events', methods=['GET'])
@token_required
@admin_required
def admin_calendar_events():
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT b.Booking_ID, b.Booking_Status, b.Booked_At,
                cal.Slot_ID, cal.Slot_Date, cal.Start_Time, cal.End_Time,
                cal.Description, cal.Slot_Type,
                a.Artist_ID, CONCAT(a.First_Name, ' ', a.Last_Name) AS artist_name,
                c.client_id, CONCAT(c.first_name, ' ', c.last_name) AS client_name
            FROM booking_table b
            INNER JOIN calendar_table cal ON b.Slot_ID = cal.Slot_ID
            INNER JOIN artist_table a ON b.Artist_ID = a.Artist_ID
            INNER JOIN client_table c ON b.Client_ID = c.client_id
            ORDER BY cal.Slot_Date DESC, cal.Start_Time DESC
        """)
        rows = cursor.fetchall()
        status_colors = {
            'confirmed': '#2ecc71', 'pending': '#f1c40f',
            'cancelled': '#e74c3c', 'completed': '#3498db'
        }
        events = []
        for row in rows:
            status = (row.get('Booking_Status') or '').lower()
            events.append({
                'id': row.get('Booking_ID'),
                'title': f"{row.get('artist_name')} - {row.get('client_name')}",
                'start': f"{row.get('Slot_Date')}T{row.get('Start_Time')}",
                'end': f"{row.get('Slot_Date')}T{row.get('End_Time')}",
                'color': status_colors.get(status, '#95a5a6'),
                'extendedProps': {
                    'artist_name': row.get('artist_name'),
                    'client_name': row.get('client_name'),
                    'booking_status': row.get('Booking_Status'),
                    'slot_type': row.get('Slot_Type'),
                    'description': row.get('Description'),
                    'booked_at': str(row.get('Booked_At')) if row.get('Booked_At') else None
                }
            })
        cursor.close()
        connection.close()
        return jsonify({'success': True, 'events': events})
    except Exception as e:
        print(f"ADMIN CALENDAR EVENTS ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/calendar/request', methods=['POST'])
@token_required
@admin_required
def admin_calendar_request():
    data = request.json or {}
    booking_id = data.get('booking_id')
    message = (data.get('message') or '').strip()
    request_type = (data.get('request_type') or 'message').strip().lower()

    if not booking_id:
        return jsonify({'success': False, 'error': 'booking_id is required'}), 400
    if request_type not in ['message', 'reschedule']:
        return jsonify({'success': False, 'error': 'Invalid request_type'}), 400
    if not message:
        return jsonify({'success': False, 'error': 'Message is required'}), 400

    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT b.Booking_ID, b.Booking_Status, cal.Slot_ID, cal.Description
            FROM booking_table b
            INNER JOIN calendar_table cal ON b.Slot_ID = cal.Slot_ID
            WHERE b.Booking_ID = %s
        """, (booking_id,))
        booking = cursor.fetchone()
        if not booking:
            cursor.close()
            connection.close()
            return jsonify({'success': False, 'error': 'Booking not found'}), 404

        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        admin_note = f"[ADMIN {stamp}] {request_type.upper()}: {message}"
        existing = (booking.get('Description') or '').strip()
        updated_desc = f"{existing}\n{admin_note}" if existing else admin_note

        cursor.execute("""
            UPDATE calendar_table SET Description = %s, Updated_At = CURRENT_TIMESTAMP
            WHERE Slot_ID = %s
        """, (updated_desc, booking['Slot_ID']))

        updated_status = booking.get('Booking_Status')
        if request_type == 'reschedule' and (updated_status or '').lower() != 'cancelled':
            cursor.execute("UPDATE booking_table SET Booking_Status = 'pending' WHERE Booking_ID = %s", (booking_id,))
            updated_status = 'pending'

        connection.commit()
        cursor.close()
        connection.close()
        return jsonify({'success': True, 'message': 'Admin request sent successfully', 'booking_status': updated_status})
    except Exception as e:
        print(f"ADMIN REQUEST ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== VERIFICATION ROUTE ==========

@app.route('/verification')
@token_required
@admin_required
def verification():
    username = request.username
    connection = create_connection()
    artists_list = []
    stats = {'total': 0, 'pending': 0, 'verified': 0, 'rejected': 0}

    if connection:
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT
                    a.Artist_ID as id,
                    CONCAT(a.First_Name, ' ', a.Last_Name) as name,
                    COALESCE(cat.category_name, 'Unknown') as category,
                    a.verification_status,
                    a.Email as email,
                    a.Phone_Number as phone,
                    TIMESTAMPDIFF(YEAR, a.dob, CURDATE()) as experience,
                    s.state_name as location,
                    a.Portfolio_Path as description,
                    a.created_at, a.Gender,
                    CASE WHEN a.is_enabled = 1 THEN 'Active' ELSE 'Inactive' END as status
                FROM artist_table a
                LEFT JOIN state_table s ON a.State_ID = s.state_id
                LEFT JOIN category_table cat ON a.category_id = cat.category_id
                ORDER BY CASE a.verification_status
                    WHEN 'pending' THEN 1 WHEN 'approved' THEN 2 WHEN 'rejected' THEN 3 ELSE 4 END,
                    a.created_at DESC
            """)
            artists_list = cursor.fetchall()
            for artist in artists_list:
                artist['verification_status'] = (artist.get('verification_status') or 'pending').lower()
            stats = {
                'total': len(artists_list),
                'pending': sum(1 for a in artists_list if a.get('verification_status') == 'pending'),
                'verified': sum(1 for a in artists_list if a.get('verification_status') == 'approved'),
                'rejected': sum(1 for a in artists_list if a.get('verification_status') == 'rejected')
            }
            cursor.close()
            connection.close()
        except Exception as e:
            print(f"VERIFICATION DATABASE ERROR: {e}")
            artists_list = []

    return render_template('verification.html', artists=artists_list, stats=stats, username=username)


# ========== API ROUTES ==========

@app.route('/get_states', methods=['GET'])
@token_required
@admin_required
def get_states():
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT state_id, state_name FROM state_table ORDER BY state_name ASC")
        rows = cursor.fetchall()
        return jsonify({'success': True, 'states': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        connection.close()


@app.route('/get_cities/<int:state_id>', methods=['GET'])
@token_required
@admin_required
def get_cities(state_id):
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT state_id FROM state_table WHERE state_id = %s", (state_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'error': 'State not found'}), 404
        cursor.execute("SELECT city_id, city_name, state_id FROM city_table WHERE state_id = %s ORDER BY city_name ASC", (state_id,))
        rows = cursor.fetchall()
        return jsonify({'success': True, 'cities': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        connection.close()


@app.route('/get_pincode/<int:city_id>', methods=['GET'])
@token_required
@admin_required
def get_pincode(city_id):
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        if not _city_table_has_pincode(cursor):
            return jsonify({'success': False, 'error': 'city_table.pincode column is missing.'}), 500
        cursor.execute("SELECT city_id, state_id, pincode FROM city_table WHERE city_id = %s", (city_id,))
        city = cursor.fetchone()
        if not city:
            return jsonify({'success': False, 'error': 'City not found'}), 404
        return jsonify({
            'success': True, 'city_id': city['city_id'], 'state_id': city['state_id'],
            'pincode': str(city['pincode']).strip() if city.get('pincode') is not None else ''
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        connection.close()


@app.route('/add_artist', methods=['POST'])
@token_required
@admin_required
def add_artist():
    data = request.json if request.is_json else request.form.to_dict()
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        uses_category_id = True
        cleaned_data, errors = _validate_artist_payload(connection, data, uses_category_id)
        if errors:
            return jsonify({'success': False, 'error': 'Validation failed', 'field_errors': errors}), 400
        artist_id = _insert_artist_record(connection, cleaned_data, uses_category_id)
        return jsonify({'success': True, 'message': 'Artist created successfully!', 'artist_id': artist_id})
    except Exception as e:
        print(f"ADD ARTIST ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()


@app.route('/api/artists', methods=['GET', 'POST'])
@token_required
@admin_required
def artists_api():
    if request.method == 'GET':
        connection = create_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT
                    a.Artist_ID as id,
                    CONCAT(a.First_Name, ' ', a.Last_Name) as name,
                    a.First_Name as first_name, a.Last_Name as last_name,
                    COALESCE(cat.category_name, 'Unknown') as category,
                    CASE WHEN a.is_enabled = 1 THEN 'Active' ELSE 'Inactive' END as status,
                    a.Gender as gender, a.Phone_Number as phone, a.Email as email,
                    COALESCE(a.experience_years, 0) as experience_years,
                    '' as pincode, a.State_ID as state_id, a.City_ID as city_id,
                    a.Portfolio_Path as description, a.verification_status, a.created_at
                FROM artist_table a
                LEFT JOIN category_table cat ON a.category_id = cat.category_id
                ORDER BY a.created_at DESC
            """)
            artists_list = cursor.fetchall()
            cursor.close()
            connection.close()
            return jsonify({'success': True, 'artists': artists_list})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    elif request.method == 'POST':
        data = request.json or {}
        connection = create_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        try:
            uses_category_id = True
            cleaned_data, errors = _validate_artist_payload(connection, data, uses_category_id)
            if errors:
                return jsonify({'success': False, 'error': 'Validation failed', 'field_errors': errors}), 400
            artist_id = _insert_artist_record(connection, cleaned_data, uses_category_id)
            return jsonify({'success': True, 'message': 'Artist created successfully!', 'artist_id': artist_id})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
        finally:
            connection.close()


@app.route('/api/artists/<int:artist_id>', methods=['GET', 'PUT', 'DELETE'])
@token_required
@admin_required
def artist_api(artist_id):
    if request.method == 'GET':
        connection = create_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT a.Artist_ID as id,
                    CONCAT(a.First_Name, ' ', a.Last_Name) as name,
                    a.First_Name as first_name, a.Last_Name as last_name,
                    COALESCE(cat.category_name, 'Unknown') as category,
                    CASE WHEN a.is_enabled = 1 THEN 'Active' ELSE 'Inactive' END as status,
                    a.Gender as gender, a.Phone_Number as phone, a.Email as email,
                    COALESCE(a.experience_years, 0) as experience_years,
                    '' as pincode, a.State_ID as state_id, a.City_ID as city_id,
                    a.Portfolio_Path as description, a.verification_status, a.created_at
                FROM artist_table a
                LEFT JOIN category_table cat ON a.category_id = cat.category_id
                WHERE a.Artist_ID = %s
            """, (artist_id,))
            artist = cursor.fetchone()
            cursor.close()
            connection.close()
            if artist:
                return jsonify({'success': True, 'artist': artist})
            return jsonify({'success': False, 'error': 'Artist not found'}), 404
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    elif request.method == 'PUT':
        data = request.json or {}
        connection = create_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        try:
            uses_category_id = True
            cleaned_data, errors = _validate_artist_payload(connection, data, uses_category_id, artist_id=artist_id)
            if errors:
                return jsonify({'success': False, 'error': 'Validation failed', 'field_errors': errors}), 400
            cursor = connection.cursor()
            cursor.execute("""
                UPDATE artist_table
                SET First_Name=%s, Last_Name=%s, Gender=%s, Phone_Number=%s, Email=%s,
                    experience_years=%s, category_id=%s, dob=%s, State_ID=%s, City_ID=%s,
                    Portfolio_Path=%s, is_enabled=%s
                WHERE Artist_ID=%s
            """, (
                cleaned_data['first_name'], cleaned_data['last_name'], cleaned_data['gender'],
                cleaned_data['phone'], cleaned_data['email'], cleaned_data['experience_years'],
                cleaned_data['category_id'], cleaned_data['dob'], cleaned_data['state_id'],
                cleaned_data['city_id'], cleaned_data['portfolio_path'],
                1 if cleaned_data.get('status') != 'Inactive' else 0, artist_id
            ))
            connection.commit()
            affected = cursor.rowcount
            cursor.close()
            connection.close()
            if affected > 0:
                return jsonify({'success': True, 'message': 'Artist updated successfully!'})
            return jsonify({'success': False, 'error': 'Artist not found'}), 404
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    elif request.method == 'DELETE':
        connection = create_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        try:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM artist_table WHERE Artist_ID=%s", (artist_id,))
            connection.commit()
            affected = cursor.rowcount
            cursor.close()
            connection.close()
            if affected > 0:
                return jsonify({'success': True, 'message': 'Artist deleted successfully!'})
            return jsonify({'success': False, 'error': 'Artist not found'}), 404
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/artists/<int:artist_id>/verify', methods=['POST'])
@token_required
@admin_required
def verify_artist(artist_id):
    data = request.json or {}
    status = _normalize_verification_status(data.get('status'))
    if status not in ['pending', 'approved', 'rejected']:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400

    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        cursor = connection.cursor()
        cursor.execute("SHOW COLUMNS FROM artist_table")
        available_columns = {row[0].lower() for row in cursor.fetchall()}

        update_clauses = ["verification_status = %s"]
        params = [status]

        if 'verification_notes' in available_columns:
            update_clauses.append("verification_notes = %s")
            params.append((data.get('notes') or '').strip() or None)
        if 'verified_by' in available_columns:
            update_clauses.append("verified_by = %s")
            params.append(data.get('verified_by') if status != 'pending' else None)
        if 'verified_at' in available_columns:
            if status == 'pending':
                update_clauses.append("verified_at = NULL")
            else:
                update_clauses.append("verified_at = NOW()")

        params.append(artist_id)
        cursor.execute(f"UPDATE artist_table SET {', '.join(update_clauses)} WHERE Artist_ID = %s", tuple(params))
        connection.commit()
        affected = cursor.rowcount
        cursor.close()
        connection.close()

        if affected > 0:
            return jsonify({'success': True, 'message': f'Artist {status} successfully!'})
        return jsonify({'success': False, 'error': 'Artist not found'}), 404
    except Exception as e:
        print(f"VERIFY ARTIST ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/clients', methods=['GET', 'POST'])
@token_required
@admin_required
def clients_api():
    if request.method == 'GET':
        connection = create_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT 
                    client_id as id,
                    CONCAT(first_name, ' ', last_name) as name,
                    first_name,
                    last_name,
                    username,
                    gender,
                    dob,
                    phone_number as phone,
                    state_id,
                    city_id,
                    is_enabled,
                    is_admin,
                    created_at,
                    CONCAT(first_name, '.', last_name, '@example.com') as email
                FROM client_table
                ORDER BY created_at DESC
            """)
            clients_list = cursor.fetchall()
            cursor.close()
            connection.close()
            return jsonify({'success': True, 'clients': clients_list})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    elif request.method == 'POST':
        data = request.json or {}
        connection = create_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        try:
            cursor = connection.cursor()
            cursor.execute("""
                INSERT INTO client_table 
                (first_name, last_name, username, password, gender, dob, phone_number, state_id, city_id, is_enabled, is_admin)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                data['first_name'], data['last_name'], data['username'], data['password'],
                data['gender'], data['dob'], data.get('phone_number') or data.get('phone'),
                data.get('state_id'), data.get('city_id'),
                data.get('is_enabled', 1), data.get('is_admin', 0)
            ))
            connection.commit()
            client_id = cursor.lastrowid
            cursor.close()
            connection.close()
            return jsonify({'success': True, 'message': 'Client created successfully!', 'client_id': client_id})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/clients/<int:client_id>', methods=['GET', 'PUT', 'DELETE'])
@token_required
@admin_required
def client_api(client_id):
    if request.method == 'GET':
        connection = create_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT client_id as id, CONCAT(first_name, ' ', last_name) as name,
                    first_name, last_name,
                    username, gender, dob,
                    phone_number as phone,
                    state_id, city_id,
                    is_enabled, is_admin, created_at
                FROM client_table WHERE client_id = %s
            """, (client_id,))
            client = cursor.fetchone()
            cursor.close()
            connection.close()
            if client:
                client['email'] = f"{client['first_name']}.{client['last_name']}@example.com"
                return jsonify({'success': True, 'client': client})
            return jsonify({'success': False, 'error': 'Client not found'}), 404
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    elif request.method == 'PUT':
        data = request.json or {}
        connection = create_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        try:
            cursor = connection.cursor()
            
            # Base query fields
            query = """
                UPDATE client_table 
                SET first_name=%s, last_name=%s, username=%s, gender=%s, dob=%s, 
                    phone_number=%s, state_id=%s, city_id=%s
            """
            params = [
                data['first_name'], data['last_name'], data['username'],
                data['gender'], data['dob'], data.get('phone_number') or data.get('phone'),
                data.get('state_id'), data.get('city_id')
            ]
            
            # Only update is_admin and is_enabled if the requester is the main admin
            if getattr(request, 'username', None) == 'admin':
                if 'is_admin' in data:
                    query += ", is_admin=%s"
                    params.append(data['is_admin'])
                if 'is_enabled' in data:
                    query += ", is_enabled=%s"
                    params.append(data['is_enabled'])
            
            query += " WHERE client_id=%s"
            params.append(client_id)
            
            cursor.execute(query, tuple(params))
            connection.commit()
            affected = cursor.rowcount
            cursor.close()
            connection.close()
            
            if affected > 0:
                return jsonify({'success': True, 'message': 'Client updated successfully!'})
            return jsonify({'success': False, 'error': 'Client not found or no changes made'}), 404
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    elif request.method == 'DELETE':
        connection = create_connection()
        if not connection:
            return jsonify({'success': False, 'error': 'Database connection failed'}), 500
        try:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM client_table WHERE client_id=%s", (client_id,))
            connection.commit()
            affected = cursor.rowcount
            cursor.close()
            connection.close()
            if affected > 0:
                return jsonify({'success': True, 'message': 'Client deleted successfully!'})
            return jsonify({'success': False, 'error': 'Client not found'}), 404
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/clients/reset-password/<int:client_id>', methods=['POST'])
@token_required
@admin_required
def reset_client_password(client_id):
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        import secrets
        import string
        new_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
        
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE client_table SET Password = %s WHERE Client_ID = %s",
            (new_password, client_id)
        )
        connection.commit()
        affected = cursor.rowcount
        cursor.close()
        connection.close()
        
        if affected > 0:
            return jsonify({
                'success': True, 
                'message': 'Password reset successfully!',
                'new_password': new_password
            })
        return jsonify({'success': False, 'error': 'Client not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bookings', methods=['GET'])
@token_required
@admin_required
def bookings_api():
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT b.Booking_ID as id,
                CONCAT(c.first_name, ' ', c.last_name) as client_name,
                CONCAT(a.First_Name, ' ', a.Last_Name) as artist_name,
                b.Booking_Status as status, b.Booked_At as created_at,
                COALESCE(p.Amount, 0) as amount, p.Payment_Status as payment_status
            FROM booking_table b
            LEFT JOIN client_table c ON b.Client_ID = c.client_id
            LEFT JOIN artist_table a ON b.Artist_ID = a.Artist_ID
            LEFT JOIN payment_table p ON b.Booking_ID = p.Booking_ID
            ORDER BY b.Booked_At DESC
        """)
        bookings_list = cursor.fetchall()
        cursor.close()
        connection.close()
        return jsonify({'success': True, 'bookings': bookings_list})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/stats', methods=['GET'])
@token_required
@admin_required
def stats_api():
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) as count FROM artist_table WHERE LOWER(COALESCE(verification_status, '')) = 'approved' AND COALESCE(is_enabled, 0) = 1")
        artist_count = cursor.fetchone()['count']
        cursor.execute("SELECT COUNT(*) as count FROM client_table")
        client_count = cursor.fetchone()['count']
        cursor.execute("SELECT COUNT(*) as count FROM booking_table WHERE LOWER(COALESCE(Booking_Status, '')) IN ('pending', 'confirmed')")
        booking_count = cursor.fetchone()['count']
        cursor.execute("SELECT COUNT(*) as count FROM artist_table WHERE LOWER(COALESCE(verification_status, '')) = 'pending'")
        pending_count = cursor.fetchone()['count']
        cursor.execute("SELECT COALESCE(SUM(Amount), 0) as total FROM payment_table WHERE LOWER(COALESCE(Payment_Status, '')) IN ('success', 'completed')")
        result = cursor.fetchone()
        total_revenue = float(result['total']) if result and result['total'] else 0
        cursor.close()
        connection.close()
        return jsonify({
            'success': True,
            'stats': {
                'total_artists': artist_count, 'total_clients': client_count,
                'total_bookings': booking_count, 'pending_verifications': pending_count,
                'total_revenue': total_revenue
            }
        })
    except Exception as e:
        print(f"STATS API ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/artist/slots', methods=['GET', 'POST'])
@token_required
def artist_slots_api():
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        artist_id = _resolve_artist_id_by_username(connection, request.username)
        if not artist_id:
            return jsonify({'success': False, 'error': 'Artist account not found'}), 403

        if request.method == 'GET':
            status_filter = (request.args.get('status') or '').strip()
            available_only = (request.args.get('available_only') or '').strip().lower() in {'1', 'true', 'yes'}
            query = """
                SELECT Slot_ID, Artist_ID, Slot_Date, Start_Time, End_Time,
                    Status, Slot_Type, Description, Created_At, Updated_At
                FROM calendar_table WHERE Artist_ID = %s
            """
            params = [artist_id]
            if available_only or status_filter.lower() == 'available':
                query += " AND Status = 'Available'"
            elif status_filter:
                query += " AND Status = %s"
                params.append(status_filter)
            query += " ORDER BY Slot_Date ASC, Start_Time ASC"
            cursor = connection.cursor(dictionary=True)
            cursor.execute(query, tuple(params))
            slots = cursor.fetchall()
            cursor.close()
            return jsonify({'success': True, 'slots': slots})

        data = request.json if request.is_json else request.form.to_dict()
        slot_date = (data.get('Slot_Date') or data.get('slot_date') or '').strip()
        start_time = (data.get('Start_Time') or data.get('start_time') or '').strip()
        end_time = (data.get('End_Time') or data.get('end_time') or '').strip()
        status = (data.get('Status') or data.get('status') or 'Available').strip()
        slot_type = (data.get('Slot_Type') or data.get('slot_type') or '').strip()
        description = (data.get('Description') or data.get('description') or '').strip()

        if not slot_date or not start_time or not end_time or not slot_type:
            return jsonify({'success': False, 'error': 'Slot_Date, Start_Time, End_Time and Slot_Type are required'}), 400
        if status not in {'Available', 'Blocked'}:
            return jsonify({'success': False, 'error': "Status must be 'Available' or 'Blocked'"}), 400
        if slot_type not in {'Communication', 'Performance'}:
            return jsonify({'success': False, 'error': "Slot_Type must be 'Communication' or 'Performance'"}), 400

        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO calendar_table (Artist_ID, Slot_Date, Start_Time, End_Time, Status, Slot_Type, Description)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (artist_id, slot_date, start_time, end_time, status, slot_type, description))
        connection.commit()
        slot_id = cursor.lastrowid
        cursor.close()
        return jsonify({'success': True, 'message': 'Slot created successfully', 'slot_id': slot_id}), 201
    except Exception as e:
        connection.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()


@app.route('/api/artist/slots/available', methods=['GET'])
@token_required
def artist_available_slots_api():
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    try:
        artist_id = _resolve_artist_id_by_username(connection, request.username)
        if not artist_id:
            return jsonify({'success': False, 'error': 'Artist account not found'}), 403
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT Slot_ID, Artist_ID, Slot_Date, Start_Time, End_Time,
                Status, Slot_Type, Description, Created_At, Updated_At
            FROM calendar_table WHERE Artist_ID = %s AND Status = 'Available'
            ORDER BY Slot_Date ASC, Start_Time ASC
        """, (artist_id,))
        slots = cursor.fetchall()
        cursor.close()
        return jsonify({'success': True, 'slots': slots})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        connection.close()


@app.route('/api/artist/bookings', methods=['POST'])
@token_required
def artist_create_booking_api():
    connection = create_connection()
    if not connection:
        return jsonify({'success': False, 'error': 'Database connection failed'}), 500
    cursor = connection.cursor(dictionary=True)
    try:
        artist_id = _resolve_artist_id_by_username(connection, request.username)
        if not artist_id:
            return jsonify({'success': False, 'error': 'Artist account not found'}), 403

        data = request.json if request.is_json else request.form.to_dict()
        client_id = data.get('Client_ID') or data.get('client_id')
        slot_id = data.get('Slot_ID') or data.get('slot_id')
        booking_status = (data.get('Booking_Status') or data.get('booking_status') or 'pending').strip()

        if not client_id or not slot_id:
            return jsonify({'success': False, 'error': 'Client_ID and Slot_ID are required'}), 400

        cursor.execute("SELECT client_id FROM client_table WHERE client_id = %s LIMIT 1", (client_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'error': 'Invalid Client_ID'}), 400

        cursor.execute("""
            SELECT Slot_ID, Artist_ID, Status FROM calendar_table
            WHERE Slot_ID = %s AND Artist_ID = %s LIMIT 1
        """, (slot_id, artist_id))
        slot_row = cursor.fetchone()
        if not slot_row:
            return jsonify({'success': False, 'error': 'Slot not found for this artist'}), 404
        if (slot_row.get('Status') or '') != 'Available':
            return jsonify({'success': False, 'error': 'Selected slot is not available'}), 409

        write_cursor = connection.cursor()
        write_cursor.execute("""
            INSERT INTO booking_table (Client_ID, Artist_ID, Slot_ID, Booking_Status)
            VALUES (%s, %s, %s, %s)
        """, (client_id, artist_id, slot_id, booking_status))
        booking_id = write_cursor.lastrowid
        write_cursor.execute("""
            UPDATE calendar_table SET Status = 'Blocked', Updated_At = CURRENT_TIMESTAMP
            WHERE Slot_ID = %s
        """, (slot_id,))
        connection.commit()
        write_cursor.close()
        return jsonify({
            'success': True, 'message': 'Booking created successfully',
            'booking_id': booking_id, 'slot_id': int(slot_id), 'booking_status': booking_status
        }), 201
    except Exception as e:
        connection.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        connection.close()


# 6. App runner
if __name__ == '__main__':
    print("\n" + "="*60)
    print("CREOVIBE ARTIST BOOKING SYSTEM - STARTING")
    print("="*60)
    print("Open: http://localhost:5000")
    print("Login: admin / admin123")
    print("="*60 + "\n")
    app.run(debug=True, port=5000)
