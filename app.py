import os
from flask import Flask, render_template, request, redirect, flash, url_for, send_from_directory, jsonify, make_response, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
from sqlalchemy import func, extract, and_, or_
from functools import wraps 
import re
import io
import csv
import calendar
from collections import defaultdict
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__, static_folder='static', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Database configuration
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///local.db').replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# File upload configuration
UPLOAD_FOLDER = os.path.join(os.getcwd(), os.environ.get('UPLOAD_FOLDER', 'static/uploads'))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif'}
MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB max file size
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

db = SQLAlchemy(app)

# Database Models
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False, unique=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())

class AttendanceRecord(db.Model):
    __tablename__ = 'attendance_record'
    
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(255), nullable=False)
    venue = db.Column(db.String(100))
    date = db.Column(db.Date)
    event = db.Column(db.String(100))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    page_count = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<AttendanceRecord {self.filename}>'

class Subcounty(db.Model):
    __tablename__ = 'subcounties'
    subcounty_id = db.Column(db.Integer, primary_key=True)
    subcounty_name = db.Column(db.String(100), nullable=False, unique=True)

class IrrigationScheme(db.Model):
    __tablename__ = 'irrigation_schemes'
    scheme_id = db.Column(db.Integer, primary_key=True)
    scheme_name = db.Column(db.String(100), nullable=False)
    subcounty_id = db.Column(db.Integer, db.ForeignKey('subcounties.subcounty_id'), nullable=False)
    scheme_type = db.Column(db.String(50))
    registration_status = db.Column(db.Enum('Self help group', 'CBO', 'Irrigation water user association'), nullable=True)
    current_status = db.Column(db.Enum('Active', 'Dormant', 'Under Construction', 'Proposed', 'Abandoned'))
    infrastructure_status = db.Column(db.Enum('Fully functional', 'Partially functional', 'Needs repair', 'Not functional', 'Not constructed'))
    water_source = db.Column(db.String(100))
    water_availability = db.Column(db.Enum('Adequate', 'Inadequate', 'Seasonal', 'No water'))
    intake_works_type = db.Column(db.String(100))
    conveyance_works_type = db.Column(db.String(100))
    application_type = db.Column(db.Enum('Sprinkler', 'Canals', 'Basin', 'Drip', 'Furrow'))
    main_crop = db.Column(db.String(100))
    scheme_area = db.Column(db.Float)
    irrigable_area = db.Column(db.Float)
    cropped_area = db.Column(db.Float)
    implementing_agency = db.Column(db.String(100))
    
    subcounty = db.relationship('Subcounty', backref='schemes')

class GPSData(db.Model):
    __tablename__ = 'gps_data'
    id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey('irrigation_schemes.scheme_id'), nullable=False)
    latitude = db.Column(db.Numeric(9,6), nullable=False)
    longitude = db.Column(db.Numeric(9,6), nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    scheme = db.relationship('IrrigationScheme', backref='gps_data')

class Assessment(db.Model):
    __tablename__ = 'assessments'
    assessment_id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey('irrigation_schemes.scheme_id'), nullable=False)
    agent_name = db.Column(db.String(100), nullable=False)
    assessment_date = db.Column(db.Date, nullable=False)
    farmers_count = db.Column(db.Integer)
    future_plans = db.Column(db.Text)
    challenges = db.Column(db.Text)
    additional_notes = db.Column(db.Text)
    created_at = db.Column(db.TIMESTAMP, nullable=False, default=datetime.utcnow)
    
    scheme = db.relationship('IrrigationScheme', backref='assessments')

class Document(db.Model):
    __tablename__ = 'documents'
    document_id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey('irrigation_schemes.scheme_id'), nullable=False)
    assessment_id = db.Column(db.Integer, db.ForeignKey('assessments.assessment_id'))
    document_type = db.Column(db.String(50), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    scheme = db.relationship('IrrigationScheme', backref='documents')
    assessment = db.relationship('Assessment', backref='documents')

class Photo(db.Model):
    __tablename__ = 'photos'
    id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey('irrigation_schemes.scheme_id'), nullable=False)
    assessment_id = db.Column(db.Integer, db.ForeignKey('assessments.assessment_id'))
    filename = db.Column(db.String(200), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    scheme = db.relationship('IrrigationScheme', backref='photos')
    assessment = db.relationship('Assessment', backref='photos')

# Helper Functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def parse_date(date_str):
    """Parse date from string in YYYY-MM-DD format"""
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None

def parse_gps_coordinates(gps_str):
    """Parse GPS coordinates from string to decimal degrees"""
    try:
        if not gps_str:
            raise ValueError("Empty GPS coordinates")
            
        cleaned = gps_str.replace('°', '').strip()
        parts = re.split(r'[,\s]+', cleaned)
        parts = [p for p in parts if p]
        
        if len(parts) == 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                pass
        
        if len(parts) == 4:
            try:
                lat = float(parts[0]) * (1 if parts[1].upper() in ['N', ''] else -1)
                lon = float(parts[2]) * (1 if parts[3].upper() in ['E', ''] else -1)
                return lat, lon
            except ValueError:
                pass
                
        raise ValueError("Could not parse GPS coordinates. Use format: '0.6341° N, 35.7364° E' or '-0.6341, 35.7364'")
    except Exception as e:
        raise ValueError(f"GPS parsing error: {str(e)}")

def save_uploaded_file(file, subfolder):
    """Save uploaded file to the specified subfolder"""
    if file and file.filename:
        if not allowed_file(file.filename):
            raise ValueError(f"File type not allowed: {file.filename}")
            
        os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], subfolder), exist_ok=True)
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], subfolder, filename)
        file.save(filepath)
        return filename, filepath
    return None, None

def validate_file(file):
    """Validate file before upload"""
    if not allowed_file(file.filename):
        return False, "Only PDF files are allowed"
    if file.content_length > MAX_CONTENT_LENGTH:
        return False, "File size exceeds 10MB limit"
    return True, ""

def format_date_key(d, time_period):
    """Format date based on time period"""
    if time_period == 'monthly':
        return d.strftime('%b %Y')
    elif time_period == 'weekly':
        year, week, _ = d.isocalendar()
        return f'W{week:02d} {year}'
    elif time_period == 'yearly':
        return d.strftime('%Y')
    else:  # daily
        return d.strftime('%Y-%m-%d')

def increment_date(d, time_period):
    """Increment date based on time period"""
    if time_period == 'monthly':
        # Get first day of next month
        if d.month == 12:
            return date(d.year + 1, 1, 1)
        return date(d.year, d.month + 1, 1)
    elif time_period == 'weekly':
        return d + timedelta(days=7)
    elif time_period == 'yearly':
        return date(d.year + 1, 1, 1)
    else:  # daily
        return d + timedelta(days=1)

def process_trend_data(results, time_period, start_date=None, end_date=None):
    """Process data for trend chart"""
    date_counts = defaultdict(int)
    
    for record in results:
        date_key = format_date_key(record.date, time_period)
        date_counts[date_key] += record.count
    
    # Fill in missing dates if range is provided
    if start_date and end_date:
        current_date = start_date
        while current_date <= end_date:
            date_key = format_date_key(current_date, time_period)
            if date_key not in date_counts:
                date_counts[date_key] = 0
            current_date = increment_date(current_date, time_period)
    
    # Sort by date
    sorted_dates = sorted(date_counts.items(), key=lambda x: x[0])
    
    return {
        'labels': [x[0] for x in sorted_dates],
        'values': [x[1] for x in sorted_dates]
    }

def process_venue_data(results):
    """Process data for venue comparison chart"""
    return {
        'labels': [x[0] for x in results if x[0]],
        'values': [x[1] for x in results if x[0]]
    }

def process_event_data(results):
    """Process data for event distribution chart"""
    return {
        'labels': [x[0] if x[0] else 'No Event' for x in results],
        'values': [x[1] for x in results]
    }

# Authentication Routes
@app.route('/')
def root():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        agent_user = os.environ.get('AGENT_USERNAME', 'Agent')
        agent_pass = os.environ.get('AGENT_PASSWORD', 'agent@2025!')
        admin_user = os.environ.get('ADMIN_USERNAME', 'CiduAdmin')
        admin_pass = os.environ.get('ADMIN_PASSWORD', 'admin@2025#')
        
        if username == agent_user and password == agent_pass:
            response = make_response(redirect(url_for('index')))
            response.set_cookie('auth_role', 'agent')
            return response
        elif username == admin_user and password == admin_pass:
            response = make_response(redirect(url_for('home')))
            response.set_cookie('auth_role', 'admin')
            return response
        else:
            flash('Invalid username or password', 'danger')
            return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    response = make_response(redirect(url_for('login')))
    response.set_cookie('auth_role', '', expires=0)
    return response

def role_required(required_role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            auth_role = request.cookies.get('auth_role')
            if not auth_role or auth_role != required_role:
                flash('You are not authorized to access this page', 'danger')
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Application Routes
@app.route('/agent')
@role_required('agent')
def index():
    return render_template('agent.html')

@app.route('/attendance')
def attendance():
    return render_template('attendance.html')

@app.route('/api/upload', methods=['POST'])
def upload_files():
    if 'files' not in request.files:
        return jsonify({'success': False, 'message': 'No files selected'}), 400
    
    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return jsonify({'success': False, 'message': 'No files selected'}), 400
    
    # Get metadata from form
    venue = request.form.get('venue', '').strip()
    date_str = request.form.get('date', '')
    event = request.form.get('event', '').strip()
    event_date = parse_date(date_str)
    
    if not venue:
        return jsonify({'success': False, 'message': 'Venue is required'}), 400
    
    if not event_date:
        return jsonify({'success': False, 'message': 'Valid date is required'}), 400
    
    uploaded_files = []
    errors = []
    
    for file in files:
        is_valid, validation_msg = validate_file(file)
        if not is_valid:
            errors.append(f"File {file.filename}: {validation_msg}")
            continue
            
        try:
            filename = secure_filename(file.filename)
            # Add timestamp to filename to avoid collisions
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            unique_filename = f"{timestamp}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            
            # Save file
            file.save(filepath)
            
            # Create database record with provided metadata
            record = AttendanceRecord(
                filename=filename,
                filepath=filepath,
                venue=venue,
                date=event_date,
                event=event,
                page_count=0  # You might want to add PDF page count extraction here
            )
            db.session.add(record)
            uploaded_files.append(filename)
        except Exception as e:
            errors.append(f"Error processing {file.filename}: {str(e)}")
    
    if errors and not uploaded_files:
        return jsonify({'success': False, 'message': 'All files failed to upload', 'errors': errors}), 400
    
    db.session.commit()
    
    response = {
        'success': True,
        'message': f'Successfully uploaded {len(uploaded_files)} files',
        'files': uploaded_files
    }
    
    if errors:
        response['errors'] = errors
        response['message'] += f', with {len(errors)} errors'
    
    return jsonify(response)

@app.route('/api/venues')
def get_venues():
    venues = db.session.query(AttendanceRecord.venue).distinct().filter(
        AttendanceRecord.venue.isnot(None)
    ).order_by(AttendanceRecord.venue).all()
    return jsonify([v[0] for v in venues if v[0]])

@app.route('/api/events')
def get_events():
    events = db.session.query(AttendanceRecord.event).distinct().filter(
        AttendanceRecord.event.isnot(None)
    ).order_by(AttendanceRecord.event).all()
    return jsonify([e[0] for e in events if e[0]])

@app.route('/api/attendance')
def get_attendance():
    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    
    # Filter parameters
    date_filter = request.args.get('date')
    venue_filter = request.args.get('venue')
    event_filter = request.args.get('event')
    
    # Sorting parameters
    sort_field = request.args.get('sort_field', 'upload_date')
    sort_order = request.args.get('sort_order', 'desc')
    
    query = AttendanceRecord.query
    
    # Apply filters
    if date_filter:
        query = query.filter(AttendanceRecord.date == date_filter)
    if venue_filter:
        query = query.filter(AttendanceRecord.venue == venue_filter)
    if event_filter:
        query = query.filter(AttendanceRecord.event == event_filter)
    
    # Apply sorting
    if sort_field and sort_order:
        sort_column = getattr(AttendanceRecord, sort_field, None)
        if sort_column is not None:
            if sort_order == 'asc':
                query = query.order_by(sort_column.asc())
            else:
                query = query.order_by(sort_column.desc())
    
    # Get paginated results
    pagination = query.paginate(
        page=page, 
        per_page=per_page,
        error_out=False
    )
    
    records = pagination.items
    
    result = []
    for record in records:
        result.append({
            'id': record.id,
            'filename': record.filename,
            'venue': record.venue,
            'date': record.date.strftime('%Y-%m-%d') if record.date else None,
            'event': record.event,
            'upload_date': record.upload_date.isoformat(),
            'page_count': record.page_count
        })
    
    return jsonify({
        'records': result,
        'total_records': pagination.total,
        'total_pages': pagination.pages,
        'current_page': pagination.page
    })

@app.route('/api/attendance/stats')
def get_attendance_stats():
    venue_filter = request.args.get('venue')
    event_filter = request.args.get('event')
    time_period = request.args.get('time_period', 'monthly')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    # Parse dates if provided
    start_date = parse_date(start_date_str) if start_date_str else None
    end_date = parse_date(end_date_str) if end_date_str else None
    
    # Validate date range
    if start_date and end_date and start_date > end_date:
        return jsonify({'success': False, 'message': 'End date must be after start date'}), 400
    
    # Initialize query for trend data
    trend_query = db.session.query(
        AttendanceRecord.date,
        func.count(AttendanceRecord.id).label('count')
    )
    
    # Initialize query for venue data
    venue_query = db.session.query(
        AttendanceRecord.venue,
        func.count(AttendanceRecord.id).label('count')
    )
    
    # Initialize query for event data
    event_query = db.session.query(
        AttendanceRecord.event,
        func.count(AttendanceRecord.id).label('count')
    )
    
    # Apply filters to all queries
    for query in [trend_query, venue_query, event_query]:
        if venue_filter:
            query = query.filter(AttendanceRecord.venue == venue_filter)
        if event_filter:
            query = query.filter(AttendanceRecord.event == event_filter)
        if start_date:
            query = query.filter(AttendanceRecord.date >= start_date)
        if end_date:
            query = query.filter(AttendanceRecord.date <= end_date)
    
    # Group trend data by time period
    if time_period == 'monthly':
        trend_query = trend_query.group_by(
            extract('year', AttendanceRecord.date),
            extract('month', AttendanceRecord.date)
        ).order_by(
            extract('year', AttendanceRecord.date),
            extract('month', AttendanceRecord.date)
        )
    elif time_period == 'weekly':
        trend_query = trend_query.group_by(
            func.yearweek(AttendanceRecord.date)
        ).order_by(
            func.yearweek(AttendanceRecord.date)
        )
    elif time_period == 'yearly':
        trend_query = trend_query.group_by(
            extract('year', AttendanceRecord.date)
        ).order_by(
            extract('year', AttendanceRecord.date)
        )
    else:  # daily
        trend_query = trend_query.group_by(
            AttendanceRecord.date
        ).order_by(
            AttendanceRecord.date
        )
    
    # Group venue data by venue
    venue_query = venue_query.group_by(
        AttendanceRecord.venue
    ).order_by(
        func.count(AttendanceRecord.id).desc()
    )
    
    # Group event data by event
    event_query = event_query.group_by(
        AttendanceRecord.event
    ).order_by(
        func.count(AttendanceRecord.id).desc()
    )
    
    # Execute queries
    trend_results = trend_query.all()
    venue_results = venue_query.all()
    event_results = event_query.all()
    
    # Process results for trend chart
    trend_data = process_trend_data(trend_results, time_period, start_date, end_date)
    
    # Process results for venue comparison
    venue_data = process_venue_data(venue_results)
    
    # Process results for event distribution
    event_data = process_event_data(event_results)
    
    return jsonify({
        'success': True,
        'trend': trend_data,
        'venues': venue_data,
        'events': event_data
    })

@app.route('/download/<int:record_id>')
def download_file(record_id):
    record = AttendanceRecord.query.get_or_404(record_id)
    if not os.path.exists(record.filepath):
        abort(404, description="File not found")
    
    return send_from_directory(
        directory=os.path.dirname(record.filepath),
        path=os.path.basename(record.filepath),
        as_attachment=True,
        download_name=record.filename
    )

@app.route('/preview/<int:record_id>')
def preview_file(record_id):
    record = AttendanceRecord.query.get_or_404(record_id)
    if not os.path.exists(record.filepath):
        abort(404, description="File not found")
    
    return send_from_directory(
        directory=os.path.dirname(record.filepath),
        path=os.path.basename(record.filepath)
    )

@app.route('/api/attendance/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    record = AttendanceRecord.query.get_or_404(record_id)
    
    try:
        # Delete file from filesystem
        if os.path.exists(record.filepath):
            os.remove(record.filepath)
        
        # Delete record from database
        db.session.delete(record)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Record deleted successfully'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Error deleting record: {str(e)}'
        }), 500

@app.route('/api/attendance/export/csv')
def export_csv():
    # Get filter parameters
    date_filter = request.args.get('date')
    venue_filter = request.args.get('venue')
    event_filter = request.args.get('event')
    
    query = AttendanceRecord.query
    
    # Apply filters
    if date_filter:
        query = query.filter(AttendanceRecord.date == date_filter)
    if venue_filter:
        query = query.filter(AttendanceRecord.venue == venue_filter)
    if event_filter:
        query = query.filter(AttendanceRecord.event == event_filter)
    
    records = query.order_by(AttendanceRecord.date.desc()).all()
    
    # Generate CSV content
    csv_content = "ID,Filename,Venue,Date,Event,Upload Date,Page Count\n"
    for record in records:
        csv_content += f"{record.id},{record.filename},{record.venue or ''},"
        csv_content += f"{record.date.strftime('%Y-%m-%d') if record.date else ''},"
        csv_content += f"{record.event or ''},"
        csv_content += f"{record.upload_date.isoformat() if record.upload_date else ''},"
        csv_content += f"{record.page_count or ''}\n"
    
    # Create response
    response = app.response_class(
        response=csv_content,
        status=200,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=attendance_records.csv'}
    )
    
    return response

@app.route('/api/attendance/export/pdf')
def export_pdf():
    # This is a placeholder - in a real implementation you would generate a PDF
    return jsonify({
        'success': False,
        'message': 'PDF export is not implemented yet'
    }), 501

@app.route('/submit', methods=['POST'])
@role_required('agent')
def submit():
    try:
        # Validate required fields
        required_fields = {
            'agentName': 'Field Agent Name',
            'visitDate': 'Assessment Date',
            'subcounty': 'Subcounty',
            'scheme': 'Irrigation Scheme',
            'gpsCoordinates': 'GPS Coordinates',
            'currentStatus': 'Current Operational Status',
            'registrationStatus': 'Registration Status'
        }
        
        missing_fields = [label for field, label in required_fields.items() 
                         if not request.form.get(field) and not request.files.get(field)]
        
        if missing_fields:
            flash(f"Missing required fields: {', '.join(missing_fields)}", 'error')
            return redirect(url_for('index'))

        # Process form data
        subcounty_name = request.form.get('subcounty')
        subcounty = Subcounty.query.filter_by(subcounty_name=subcounty_name).first()
        if not subcounty:
            subcounty = Subcounty(subcounty_name=subcounty_name)
            db.session.add(subcounty)
            db.session.flush()

        scheme_name_with_type = request.form.get('scheme')
        scheme_name = scheme_name_with_type
        scheme_type = 'Community'

        if '(' in scheme_name_with_type and ')' in scheme_name_with_type:
            try:
                scheme_name = scheme_name_with_type.split(' (')[0]
                scheme_type = scheme_name_with_type.split(' (')[1][:-1]
            except:
                pass

        # Create new scheme
        scheme = IrrigationScheme(
            scheme_name=scheme_name,
            subcounty_id=subcounty.subcounty_id,
            scheme_type=scheme_type,
            registration_status=request.form.get('registrationStatus'),
            current_status=request.form.get('currentStatus'),
            infrastructure_status=request.form.get('infrastructureStatus'),
            water_source=request.form.get('waterSource'),
            water_availability=request.form.get('waterAvailability'),
            intake_works_type=request.form.get('intakeWorksType'),
            conveyance_works_type=request.form.get('conveyanceWorksType'),
            application_type=request.form.get('applicationType'),
            main_crop=request.form.get('mainCrop'),
            scheme_area=float(request.form.get('schemeArea', 0)) if request.form.get('schemeArea') else None,
            irrigable_area=float(request.form.get('irrigableArea', 0)) if request.form.get('irrigableArea') else None,
            cropped_area=float(request.form.get('croppedArea', 0)) if request.form.get('croppedArea') else None,
            implementing_agency=request.form.get('implementingAgency')
        )
        db.session.add(scheme)
        db.session.flush()

        # Save GPS data
        try:
            lat, lon = parse_gps_coordinates(request.form.get('gpsCoordinates'))
            gps = GPSData(
                scheme_id=scheme.scheme_id,
                latitude=lat,
                longitude=lon
            )
            db.session.add(gps)
        except ValueError as e:
            db.session.rollback()
            flash(f"Error processing GPS coordinates: {str(e)}", 'error')
            return redirect(url_for('index'))

        # Save assessment
        assessment = Assessment(
            scheme_id=scheme.scheme_id,
            agent_name=request.form.get('agentName'),
            assessment_date=datetime.strptime(request.form.get('visitDate'), '%Y-%m-%d').date(),
            farmers_count=int(request.form.get('farmersCount', 0)) if request.form.get('farmersCount') else None,
            future_plans=request.form.get('futurePlans'),
            challenges=request.form.get('challenges'),
            additional_notes=request.form.get('additionalNotes')
        )
        db.session.add(assessment)
        db.session.flush()

        # Save documents
        doc_types = {
            'officeBearersPdf': 'office_bearers',
            'schemeMembersPdf': 'members_list',
            'bylawsPdf': 'bylaws',
            'schemeMapPdf': 'scheme_map',
            'intakeDesignsPdf': 'intake_designs',
            'feasibilityReport': 'feasibility_report',
            'esiaReport': 'esia_report',
            'wraLicensing': 'wra_licensing'
        }

        for field, doc_type in doc_types.items():
            file = request.files.get(field)
            if file and file.filename:
                try:
                    filename, filepath = save_uploaded_file(file, 'documents')
                    if filename:
                        doc = Document(
                            scheme_id=scheme.scheme_id,
                            assessment_id=assessment.assessment_id,
                            document_type=doc_type,
                            file_name=filename,
                            file_path=filepath
                        )
                        db.session.add(doc)
                except ValueError as e:
                    db.session.rollback()
                    flash(f"Error with {doc_type.replace('_', ' ')}: {str(e)}", 'error')
                    return redirect(url_for('index'))

        # Save photos
        photos = request.files.getlist('photos')
        for photo in photos:
            if photo and photo.filename:
                try:
                    filename, filepath = save_uploaded_file(photo, 'photos')
                    if filename:
                        photo_record = Photo(
                            scheme_id=scheme.scheme_id,
                            assessment_id=assessment.assessment_id,
                            filename=filename,
                            file_path=filepath
                        )
                        db.session.add(photo_record)
                except ValueError as e:
                    db.session.rollback()
                    flash(f"Error with photo upload: {str(e)}", 'error')
                    return redirect(url_for('index'))

        db.session.commit()
        flash('✅ Data submitted successfully!', 'success')
        return redirect(url_for('index'))

    except Exception as e:
        db.session.rollback()
        flash(f"An unexpected error occurred: {str(e)}", 'error')
        return redirect(url_for('index'))

# Dashboard Route
@app.route('/dashboard')
def dashboard():
    try:
        # Basic statistics
        total_schemes = IrrigationScheme.query.count()
        
        # Scheme types
        schemes_by_type = db.session.query(
            IrrigationScheme.scheme_type, 
            func.count(IrrigationScheme.scheme_id).label('total')
        ).filter(IrrigationScheme.scheme_type.isnot(None)).group_by(IrrigationScheme.scheme_type).all()
        
        schemes_by_type = [{'scheme_type': st[0] or 'Unknown', 'total': st[1]} for st in schemes_by_type]
        
        # Registration status
        registration_status = db.session.query(
            IrrigationScheme.registration_status, 
            func.count(IrrigationScheme.scheme_id).label('total')
        ).filter(IrrigationScheme.registration_status.isnot(None)).group_by(IrrigationScheme.registration_status).all()
        
        registration_status = [{'registration_status': rs[0] or 'Unregistered', 'total': rs[1]} for rs in registration_status]
        
        # Add unregistered count
        registered_count = sum([rs['total'] for rs in registration_status])
        unregistered_count = total_schemes - registered_count
        if unregistered_count > 0:
            registration_status.append({'registration_status': 'Unregistered', 'total': unregistered_count})
        
        # Schemes by subcounty
        schemes_by_subcounty = db.session.query(
            Subcounty.subcounty_name, 
            func.count(IrrigationScheme.scheme_id).label('total')
        ).join(IrrigationScheme).group_by(Subcounty.subcounty_name).all()
        
        schemes_by_subcounty = [{'subcounty_name': sc[0], 'total': sc[1]} for sc in schemes_by_subcounty]
        
        # Area analysis
        large_schemes = IrrigationScheme.query.filter(IrrigationScheme.scheme_area > 500).count()
        small_schemes = IrrigationScheme.query.filter(IrrigationScheme.scheme_area < 200).count()
        
        # Document counts
        esia = Document.query.filter(Document.document_type == 'esia_report').count()
        feasibility = Document.query.filter(Document.document_type == 'feasibility_report').count()
        wra = Document.query.filter(Document.document_type == 'wra_licensing').count()
        
        # Registration body counts
        iwua = IrrigationScheme.query.filter(
            IrrigationScheme.registration_status == 'Irrigation water user association').count()
        cbo = IrrigationScheme.query.filter(IrrigationScheme.registration_status == 'CBO').count()
        shg = IrrigationScheme.query.filter(IrrigationScheme.registration_status == 'Self help group').count()
        
        # Get all entries for table
        entries = db.session.query(
            IrrigationScheme, 
            Subcounty.subcounty_name,
            GPSData.latitude,
            GPSData.longitude
        ).join(
            Subcounty, IrrigationScheme.subcounty_id == Subcounty.subcounty_id
        ).outerjoin(
            GPSData, IrrigationScheme.scheme_id == GPSData.scheme_id
        ).all()
        
        # Status normalization mapping
        def normalize_status(status):
            if not status:
                return 'unknown'
            status = status.lower().strip()
            if 'active' in status:
                return 'active'
            elif 'dormant' in status:
                return 'dormant'
            elif 'construct' in status or 'ongoing' in status:
                return 'under-construction'
            elif 'proposed' in status or 'planned' in status:
                return 'proposed'
            return 'unknown'

        # Format entries for template
        formatted_entries = []
        scheme_coordinates = []
        
        for scheme, subcounty_name, lat, lon in entries:
            # Get document status
            scheme_docs = Document.query.filter_by(scheme_id=scheme.scheme_id).all()
            doc_types = [doc.document_type for doc in scheme_docs]
            
            # Normalize status for map filtering
            normalized_status = normalize_status(scheme.current_status)
            
            entry = {
                'id': scheme.scheme_id,
                'scheme_name': scheme.scheme_name or 'N/A',
                'subcounty_name': subcounty_name or 'N/A',
                'scheme_type': scheme.scheme_type or 'N/A',
                'registration_status': scheme.registration_status or 'Unregistered',
                'current_status': scheme.current_status or 'N/A',  # Original status for display
                'area_size': scheme.scheme_area or 0,
                'esia_status': 'Yes' if 'esia_report' in doc_types else 'No',
                'feasibility_study': 'Yes' if 'feasibility_report' in doc_types else 'No',
                'wra_license': 'Yes' if 'wra_licensing' in doc_types else 'No',
                'main_crop': scheme.main_crop or 'N/A',
                'water_source': scheme.water_source or 'N/A'
            }
            formatted_entries.append(entry)
            
            # Prepare coordinates data for map
            if lat is not None and lon is not None:
                scheme_coordinates.append({
                    'scheme_id': scheme.scheme_id,
                    'scheme_name': scheme.scheme_name,
                    'latitude': float(lat),
                    'longitude': float(lon),
                    'scheme_type': scheme.scheme_type,
                    'subcounty_name': subcounty_name,
                    'area_size': scheme.scheme_area,
                    'current_status': normalized_status,  # Normalized status for filtering
                    'original_status': scheme.current_status or 'Unknown'  # Original for display
                })
        
        return render_template('dashboard.html',
            total_schemes=total_schemes,
            schemes_by_type=schemes_by_type,
            registration_status=registration_status,
            schemes_by_subcounty=schemes_by_subcounty,
            large_schemes=large_schemes,
            small_schemes=small_schemes,
            esia=esia,
            feasibility=feasibility,
            wra=wra,
            iwua=iwua,
            cbo=cbo,
            shg=shg,
            entries=formatted_entries,
            scheme_coordinates=scheme_coordinates,
            status_colors={
                'active': '#16a34a',
                'dormant': '#dc2626',
                'under-construction': '#d97706',
                'proposed': '#7c3aed',
                'unknown': '#1a5f23'
            }
        )
        
    except Exception as e:
        app.logger.error(f"Dashboard error: {str(e)}", exc_info=True)
        return render_template('error.html', message="Could not load dashboard data"), 500

# Analytics API Route
@app.route('/api/analytics-data')
def analytics_data():
    """API endpoint to provide analytics data for the dashboard"""
    try:
        # Water Availability by Subcounty
        water_availability_query = db.session.query(
            Subcounty.subcounty_name,
            IrrigationScheme.water_availability,
            func.count(IrrigationScheme.scheme_id).label('count')
        ).join(
            IrrigationScheme, Subcounty.subcounty_id == IrrigationScheme.subcounty_id
        ).filter(
            IrrigationScheme.water_availability.isnot(None)
        ).group_by(
            Subcounty.subcounty_name, 
            IrrigationScheme.water_availability
        ).all()

        # Process water availability data
        water_availability_data = {}
        subcounties = set()
        
        for subcounty, availability, count in water_availability_query:
            subcounties.add(subcounty)
            if availability not in water_availability_data:
                water_availability_data[availability] = {}
            water_availability_data[availability][subcounty] = count

        # Fill missing combinations with 0
        subcounties = sorted(list(subcounties))
        water_categories = ['Adequate', 'Inadequate', 'Seasonal', 'No water']
        
        for category in water_categories:
            if category not in water_availability_data:
                water_availability_data[category] = {}
            for subcounty in subcounties:
                if subcounty not in water_availability_data[category]:
                    water_availability_data[category][subcounty] = 0

        # Infrastructure Status Distribution
        infrastructure_query = db.session.query(
            IrrigationScheme.infrastructure_status,
            func.count(IrrigationScheme.scheme_id).label('count')
        ).filter(
            IrrigationScheme.infrastructure_status.isnot(None)
        ).group_by(
            IrrigationScheme.infrastructure_status
        ).all()

        infrastructure_data = {}
        for status, count in infrastructure_query:
            infrastructure_data[status] = count

        # Irrigation Application Methods
        application_query = db.session.query(
            IrrigationScheme.application_type,
            func.count(IrrigationScheme.scheme_id).label('count')
        ).filter(
            IrrigationScheme.application_type.isnot(None)
        ).group_by(
            IrrigationScheme.application_type
        ).all()

        application_data = {}
        for method, count in application_query:
            application_data[method] = count

        # Calculate statistics
        total_schemes = IrrigationScheme.query.count()
        
        functional_statuses = ['Fully functional', 'Partially functional']
        functional_count = IrrigationScheme.query.filter(
            IrrigationScheme.infrastructure_status.in_(functional_statuses)
        ).count()
        
        functional_rate = round((functional_count / total_schemes * 100)) if total_schemes > 0 else 0

        # Current Status Distribution for additional insights
        current_status_query = db.session.query(
            IrrigationScheme.current_status,
            func.count(IrrigationScheme.scheme_id).label('count')
        ).filter(
            IrrigationScheme.current_status.isnot(None)
        ).group_by(
            IrrigationScheme.current_status
        ).all()

        current_status_data = {}
        for status, count in current_status_query:
            current_status_data[status] = count

        # Registration Status Distribution
        registration_query = db.session.query(
            IrrigationScheme.registration_status,
            func.count(IrrigationScheme.scheme_id).label('count')
        ).filter(
            IrrigationScheme.registration_status.isnot(None)
        ).group_by(
            IrrigationScheme.registration_status
        ).all()

        registration_data = {}
        for status, count in registration_query:
            registration_data[status] = count

        # Add unregistered schemes
        registered_count = sum(registration_data.values())
        unregistered_count = total_schemes - registered_count
        if unregistered_count > 0:
            registration_data['Unregistered'] = unregistered_count

        return {
            'success': True,
            'data': {
                'water_availability': {
                    'subcounties': subcounties,
                    'categories': water_availability_data
                },
                'infrastructure_status': infrastructure_data,
                'application_methods': application_data,
                'current_status': current_status_data,
                'registration_status': registration_data,
                'statistics': {
                    'total_schemes': total_schemes,
                    'functional_rate': functional_rate,
                    'functional_count': functional_count
                }
            }
        }

    except Exception as e:
        app.logger.error(f"Analytics data error: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }, 500

@app.route('/analytics')
def analytics_dashboard():
    """Route to serve the analytics dashboard"""
    return render_template('analytics.html')

# File Management Route
@app.route('/file')
def file_management():
    try:
        # Get all subcounties for the filter dropdown
        subcounties = Subcounty.query.order_by(Subcounty.subcounty_name).all()
        subcounty_list = [sc.subcounty_name for sc in subcounties]

        # Get all schemes for the filter dropdown
        schemes = IrrigationScheme.query.order_by(IrrigationScheme.scheme_name).all()
        scheme_list = [s.scheme_name for s in schemes]

        # Get all files (documents and photos) with associated scheme and subcounty info
        documents = db.session.query(
            Document.document_id.label('id'),
            Document.file_name.label('filename'),
            Document.file_path,
            Document.document_type,
            Document.uploaded_at,
            func.coalesce(func.length(Document.file_path), 0).label('file_size'),
            IrrigationScheme.scheme_name,
            Subcounty.subcounty_name,
            db.literal('documents').label('file_type')
        ).join(
            IrrigationScheme, Document.scheme_id == IrrigationScheme.scheme_id
        ).join(
            Subcounty, IrrigationScheme.subcounty_id == Subcounty.subcounty_id
        ).all()

        photos = db.session.query(
            Photo.id,
            Photo.filename,
            Photo.file_path,
            db.literal('image').label('document_type'),
            Photo.uploaded_at,
            func.coalesce(func.length(Photo.file_path), 0).label('file_size'),
            IrrigationScheme.scheme_name,
            Subcounty.subcounty_name,
            db.literal('photos').label('file_type')
        ).join(
            IrrigationScheme, Photo.scheme_id == IrrigationScheme.scheme_id
        ).join(
            Subcounty, IrrigationScheme.subcounty_id == Subcounty.subcounty_id
        ).all()

        # Combine documents and photos
        all_files = documents + photos

        # Convert to dictionaries for JSON serialization
        files_data = []
        for file in all_files:
            files_data.append({
                'id': file.id,
                'filename': file.filename,
                'file_path': file.file_path,
                'document_type': file.document_type,
                'uploaded_at': file.uploaded_at.isoformat() if file.uploaded_at else None,
                'file_size': file.file_size,
                'scheme_name': file.scheme_name,
                'subcounty_name': file.subcounty_name,
                'file_type': file.file_type,
                'thumbnail': url_for('static', filename=file.file_path) if file.file_type == 'photos' else None,
                'download_url': url_for('download_document', doc_id=file.id) if file.file_type == 'documents' else url_for('download_photo', photo_id=file.id),
                'share_url': url_for('file_management', _external=True) + f'#file-{file.id}'
            })

        # Calculate stats
        stats = {
            'total_files': len(files_data),
            'total_documents': len(documents),
            'total_photos': len(photos),
            'total_schemes': len(scheme_list)
        }

        return render_template('file.html',
                            files=files_data,
                            subcounties=subcounty_list,
                            schemes=scheme_list,
                            stats=stats)

    except Exception as e:
        app.logger.error(f"Error in file management route: {str(e)}")
        flash("An error occurred while loading files. Please try again.", "error")
        return render_template('file.html',
                            files=[],
                            subcounties=[],
                            schemes=[],
                            stats={
                                'total_files': 0,
                                'total_documents': 0,
                                'total_photos': 0,
                                'total_schemes': 0
                            })

# Download routes
@app.route('/download/documents/<int:doc_id>')
def download_document(doc_id):
    document = Document.query.get_or_404(doc_id)
    # Extract the relative path from the stored file_path
    relative_path = os.path.relpath(document.file_path, 'static')
    directory = os.path.dirname(os.path.join(app.root_path, 'static', relative_path))
    filename = os.path.basename(document.file_path)
    return send_from_directory(
        directory,
        filename,
        as_attachment=True,
        download_name=document.file_name
    )

@app.route('/download/photos/<int:photo_id>')
def download_photo(photo_id):
    photo = Photo.query.get_or_404(photo_id)
    # Extract the relative path from the stored file_path
    relative_path = os.path.relpath(photo.file_path, 'static')
    directory = os.path.dirname(os.path.join(app.root_path, 'static', relative_path))
    filename = os.path.basename(photo.file_path)
    return send_from_directory(
        directory,
        filename,
        as_attachment=True,
        download_name=photo.filename
    )

# Add template filters and context processor
@app.template_filter('format_file_size')
def format_file_size(size):
    if not size or size == 0:
        return '0 Bytes'
    for unit in ['Bytes', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"

@app.template_filter('format_date')
def format_date(date_string):
    if not date_string:
        return 'Unknown'
    try:
        date = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        return date.strftime("%b %d, %Y")
    except ValueError:
        return date_string

@app.context_processor
def utility_processor():
    def get_file_icon(file_type):
        icons = {
            'pdf': {'icon': 'fas fa-file-pdf', 'color': '#e74c3c', 'background': '#e74c3c'},
            'image': {'icon': 'fas fa-file-image', 'color': '#3498db', 'background': '#3498db'},
            'jpg': {'icon': 'fas fa-file-image', 'color': '#3498db', 'background': '#3498db'},
            'jpeg': {'icon': 'fas fa-file-image', 'color': '#3498db', 'background': '#3498db'},
            'png': {'icon': 'fas fa-file-image', 'color': '#3498db', 'background': '#3498db'},
            'gif': {'icon': 'fas fa-file-image', 'color': '#3498db', 'background': '#3498db'},
            'doc': {'icon': 'fas fa-file-word', 'color': '#2980b9', 'background': '#2980b9'},
            'docx': {'icon': 'fas fa-file-word', 'color': '#2980b9', 'background': '#2980b9'},
            'xls': {'icon': 'fas fa-file-excel', 'color': '#2ecc71', 'background': '#2ecc71'},
            'xlsx': {'icon': 'fas fa-file-excel', 'color': '#2ecc71', 'background': '#2ecc71'},
            'txt': {'icon': 'fas fa-file-alt', 'color': '#6c757d', 'background': '#6c757d'},
        }
        return icons.get(file_type.lower(), {'icon': 'fas fa-file-alt', 'color': '#667eea', 'background': '#667eea'})
    return {'get_file_icon': get_file_icon}

# Assessments Routes
@app.route('/assessments')
def assessments_dashboard():
    """Route to serve the assessments dashboard"""
    return render_template('assessments.html')

@app.route('/api/subcounties')
def api_subcounties():
    """API endpoint to get all subcounties"""
    try:
        subcounties = Subcounty.query.order_by(Subcounty.subcounty_name).all()
        return jsonify([{
            'subcounty_id': sc.subcounty_id,
            'subcounty_name': sc.subcounty_name
        } for sc in subcounties])
    except Exception as e:
        app.logger.error(f"Error fetching subcounties: {str(e)}")
        return jsonify({'error': 'Failed to fetch subcounties'}), 500

@app.route('/api/schemes')
def api_schemes():
    """API endpoint to get schemes, optionally filtered by subcounty"""
    try:
        subcounty_id = request.args.get('subcounty_id')
        
        query = IrrigationScheme.query
        if subcounty_id:
            query = query.filter_by(subcounty_id=subcounty_id)
        
        schemes = query.order_by(IrrigationScheme.scheme_name).all()
        return jsonify([{
            'scheme_id': s.scheme_id,
            'scheme_name': s.scheme_name,
            'subcounty_id': s.subcounty_id
        } for s in schemes])
    except Exception as e:
        app.logger.error(f"Error fetching schemes: {str(e)}")
        return jsonify({'error': 'Failed to fetch schemes'}), 500

@app.route('/api/assessments')
def api_assessments():
    """API endpoint to get all assessments with scheme and subcounty data"""
    try:
        assessments = db.session.query(
            Assessment.assessment_id,
            Assessment.scheme_id,
            Assessment.agent_name,
            Assessment.assessment_date,
            Assessment.farmers_count,
            Assessment.future_plans,
            Assessment.challenges,
            Assessment.additional_notes,
            Assessment.created_at,
            IrrigationScheme.scheme_name,
            IrrigationScheme.current_status,
            IrrigationScheme.water_availability,
            IrrigationScheme.infrastructure_status,
            IrrigationScheme.main_crop,
            IrrigationScheme.scheme_area,
            Subcounty.subcounty_name,
            Subcounty.subcounty_id
        ).join(
            IrrigationScheme, Assessment.scheme_id == IrrigationScheme.scheme_id
        ).join(
            Subcounty, IrrigationScheme.subcounty_id == Subcounty.subcounty_id
        ).order_by(
            Assessment.assessment_date.desc()
        ).all()

        return jsonify({
            'assessments': [{
                'assessment_id': a.assessment_id,
                'scheme_id': a.scheme_id,
                'agent_name': a.agent_name,
                'assessment_date': a.assessment_date.isoformat() if a.assessment_date else None,
                'farmers_count': a.farmers_count,
                'future_plans': a.future_plans,
                'challenges': a.challenges,
                'additional_notes': a.additional_notes,
                'created_at': a.created_at.isoformat() if a.created_at else None,
                'scheme_name': a.scheme_name,
                'current_status': a.current_status,
                'water_availability': a.water_availability,
                'infrastructure_status': a.infrastructure_status,
                'main_crop': a.main_crop,
                'scheme_area': float(a.scheme_area) if a.scheme_area else None,
                'subcounty_name': a.subcounty_name,
                'subcounty_id': a.subcounty_id
            } for a in assessments]
        })
    except Exception as e:
        app.logger.error(f"Error fetching assessments: {str(e)}")
        return jsonify({'error': 'Failed to fetch assessments'}), 500

@app.route('/api/assessments/<int:assessment_id>')
def api_assessment_details(assessment_id):
    """API endpoint to get detailed assessment data"""
    try:
        assessment = db.session.query(
            Assessment.assessment_id,
            Assessment.scheme_id,
            Assessment.agent_name,
            Assessment.assessment_date,
            Assessment.farmers_count,
            Assessment.future_plans,
            Assessment.challenges,
            Assessment.additional_notes,
            Assessment.created_at,
            IrrigationScheme.scheme_name,
            IrrigationScheme.current_status,
            IrrigationScheme.water_availability,
            IrrigationScheme.infrastructure_status,
            IrrigationScheme.main_crop,
            IrrigationScheme.scheme_area,
            IrrigationScheme.irrigable_area,
            IrrigationScheme.cropped_area,
            IrrigationScheme.water_source,
            IrrigationScheme.intake_works_type,
            IrrigationScheme.conveyance_works_type,
            IrrigationScheme.application_type,
            IrrigationScheme.implementing_agency,
            Subcounty.subcounty_name,
            Subcounty.subcounty_id
        ).join(
            IrrigationScheme, Assessment.scheme_id == IrrigationScheme.scheme_id
        ).join(
            Subcounty, IrrigationScheme.subcounty_id == Subcounty.subcounty_id
        ).filter(
            Assessment.assessment_id == assessment_id
        ).first()

        if not assessment:
            return jsonify({'error': 'Assessment not found'}), 404

        # Get associated documents
        documents = Document.query.filter_by(assessment_id=assessment_id).all()
        photos = Photo.query.filter_by(assessment_id=assessment_id).all()

        return jsonify({
            'assessment_id': assessment.assessment_id,
            'scheme_id': assessment.scheme_id,
            'agent_name': assessment.agent_name,
            'assessment_date': assessment.assessment_date.isoformat() if assessment.assessment_date else None,
            'farmers_count': assessment.farmers_count,
            'future_plans': assessment.future_plans,
            'challenges': assessment.challenges,
            'additional_notes': assessment.additional_notes,
            'created_at': assessment.created_at.isoformat() if assessment.created_at else None,
            'scheme_name': assessment.scheme_name,
            'current_status': assessment.current_status,
            'water_availability': assessment.water_availability,
            'infrastructure_status': assessment.infrastructure_status,
            'main_crop': assessment.main_crop,
            'scheme_area': float(assessment.scheme_area) if assessment.scheme_area else None,
            'irrigable_area': float(assessment.irrigable_area) if assessment.irrigable_area else None,
            'cropped_area': float(assessment.cropped_area) if assessment.cropped_area else None,
            'water_source': assessment.water_source,
            'intake_works_type': assessment.intake_works_type,
            'conveyance_works_type': assessment.conveyance_works_type,
            'application_type': assessment.application_type,
            'implementing_agency': assessment.implementing_agency,
            'subcounty_name': assessment.subcounty_name,
            'subcounty_id': assessment.subcounty_id,
            'documents': [{
                'document_id': d.document_id,
                'document_type': d.document_type,
                'file_name': d.file_name,
                'file_path': d.file_path
            } for d in documents],
            'photos': [{
                'id': p.id,
                'filename': p.filename,
                'file_path': p.file_path
            } for p in photos]
        })
    except Exception as e:
        app.logger.error(f"Error fetching assessment details: {str(e)}")
        return jsonify({'error': 'Failed to fetch assessment details'}), 500

@app.route('/api/assessments/export')
def export_assessments():
    """Export assessments data as CSV"""
    try:
        # Get filter parameters
        subcounty_id = request.args.get('subcounty_id')
        scheme_id = request.args.get('scheme_id')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        # Build query
        query = db.session.query(
            Assessment.assessment_id,
            Assessment.scheme_id,
            Assessment.agent_name,
            Assessment.assessment_date,
            Assessment.farmers_count,
            Assessment.future_plans,
            Assessment.challenges,
            Assessment.additional_notes,
            Assessment.created_at,
            IrrigationScheme.scheme_name,
            IrrigationScheme.current_status,
            IrrigationScheme.water_availability,
            IrrigationScheme.infrastructure_status,
            IrrigationScheme.main_crop,
            IrrigationScheme.scheme_area,
            Subcounty.subcounty_name
        ).join(
            IrrigationScheme, Assessment.scheme_id == IrrigationScheme.scheme_id
        ).join(
            Subcounty, IrrigationScheme.subcounty_id == Subcounty.subcounty_id
        )

        if subcounty_id:
            query = query.filter(Subcounty.subcounty_id == subcounty_id)
        if scheme_id:
            query = query.filter(IrrigationScheme.scheme_id == scheme_id)
        if start_date:
            query = query.filter(Assessment.assessment_date >= start_date)
        if end_date:
            query = query.filter(Assessment.assessment_date <= end_date)

        assessments = query.order_by(Assessment.assessment_date.desc()).all()

        # Create CSV output
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            'Assessment ID', 'Scheme ID', 'Scheme Name', 'Subcounty',
            'Agent Name', 'Assessment Date', 'Farmers Count',
            'Current Status', 'Water Availability', 'Infrastructure Status',
            'Main Crop', 'Scheme Area (acres)', 'Future Plans',
            'Challenges', 'Additional Notes', 'Created At'
        ])
        
        # Write data
        for a in assessments:
            writer.writerow([
                a.assessment_id,
                a.scheme_id,
                a.scheme_name,
                a.subcounty_name,
                a.agent_name,
                a.assessment_date.isoformat() if a.assessment_date else '',
                a.farmers_count,
                a.current_status,
                a.water_availability,
                a.infrastructure_status,
                a.main_crop,
                float(a.scheme_area) if a.scheme_area else '',
                a.future_plans or '',
                a.challenges or '',
                a.additional_notes or '',
                a.created_at.isoformat() if a.created_at else ''
            ])
        
        # Create response
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = 'attachment; filename=assessments_export.csv'
        response.headers['Content-type'] = 'text/csv'
        return response

    except Exception as e:
        app.logger.error(f"Error exporting assessments: {str(e)}")
        return jsonify({'error': 'Failed to export assessments'}), 500

@app.route('/api/assessments/<int:assessment_id>/export')
def export_single_assessment(assessment_id):
    """Export single assessment as CSV"""
    try:
        assessment = db.session.query(
            Assessment.assessment_id,
            Assessment.scheme_id,
            Assessment.agent_name,
            Assessment.assessment_date,
            Assessment.farmers_count,
            Assessment.future_plans,
            Assessment.challenges,
            Assessment.additional_notes,
            Assessment.created_at,
            IrrigationScheme.scheme_name,
            IrrigationScheme.current_status,
            IrrigationScheme.water_availability,
            IrrigationScheme.infrastructure_status,
            IrrigationScheme.main_crop,
            IrrigationScheme.scheme_area,
            IrrigationScheme.irrigable_area,
            IrrigationScheme.cropped_area,
            IrrigationScheme.water_source,
            IrrigationScheme.intake_works_type,
            IrrigationScheme.conveyance_works_type,
            IrrigationScheme.application_type,
            IrrigationScheme.implementing_agency,
            Subcounty.subcounty_name
        ).join(
            IrrigationScheme, Assessment.scheme_id == IrrigationScheme.scheme_id
        ).join(
            Subcounty, IrrigationScheme.subcounty_id == Subcounty.subcounty_id
        ).filter(
            Assessment.assessment_id == assessment_id
        ).first()

        if not assessment:
            return jsonify({'error': 'Assessment not found'}), 404

        # Create CSV output
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['Field', 'Value'])
        
        # Write data
        writer.writerow(['Assessment ID', assessment.assessment_id])
        writer.writerow(['Scheme ID', assessment.scheme_id])
        writer.writerow(['Scheme Name', assessment.scheme_name])
        writer.writerow(['Subcounty', assessment.subcounty_name])
        writer.writerow(['Agent Name', assessment.agent_name])
        writer.writerow(['Assessment Date', assessment.assessment_date.isoformat() if assessment.assessment_date else ''])
        writer.writerow(['Farmers Count', assessment.farmers_count])
        writer.writerow(['Current Status', assessment.current_status])
        writer.writerow(['Water Availability', assessment.water_availability])
        writer.writerow(['Infrastructure Status', assessment.infrastructure_status])
        writer.writerow(['Main Crop', assessment.main_crop])
        writer.writerow(['Scheme Area (acres)', float(assessment.scheme_area) if assessment.scheme_area else ''])
        writer.writerow(['Irrigable Area (acres)', float(assessment.irrigable_area) if assessment.irrigable_area else ''])
        writer.writerow(['Cropped Area (acres)', float(assessment.cropped_area) if assessment.cropped_area else ''])
        writer.writerow(['Water Source', assessment.water_source])
        writer.writerow(['Intake Works Type', assessment.intake_works_type])
        writer.writerow(['Conveyance Works Type', assessment.conveyance_works_type])
        writer.writerow(['Application Type', assessment.application_type])
        writer.writerow(['Implementing Agency', assessment.implementing_agency])
        writer.writerow(['Future Plans', assessment.future_plans or ''])
        writer.writerow(['Challenges', assessment.challenges or ''])
        writer.writerow(['Additional Notes', assessment.additional_notes or ''])
        writer.writerow(['Created At', assessment.created_at.isoformat() if assessment.created_at else ''])
        
        # Create response
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=assessment_{assessment_id}_export.csv'
        response.headers['Content-type'] = 'text/csv'
        return response

    except Exception as e:
        app.logger.error(f"Error exporting assessment: {str(e)}")
        return jsonify({'error': 'Failed to export assessment'}), 500

@app.route('/home')
@role_required('admin')
def home():
    return render_template('home.html')

# Initialize database
try:
    with app.app_context():
        db.create_all()
        app.logger.info("Database tables created successfully")
except Exception as e:
    app.logger.error(f"Failed to initialize database: {str(e)}")
    raise

# Production configuration
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_DEBUG', 'False') == 'True')
