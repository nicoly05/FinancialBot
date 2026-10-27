from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate, upgrade
from sqlalchemy import inspect, text
import bcrypt
import json
import os
import re
from datetime import datetime, timedelta, date
from config import Config
import requests
from planning_engine import compute_financial_plan
from chat_engine import fold as fold_chat
from chat_engine import interpret_message
from werkzeug.utils import secure_filename
from datetime import datetime


def _get_supabase():
    url = Config.SUPABASE_URL
    key = Config.SUPABASE_KEY
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        print(f'Supabase indisponível: {exc}')
        return None


def upload_image_to_supabase(file, folder):
    """Upload de imagem para Supabase Storage"""
    try:
        client = _get_supabase()
        if client is None:
            return None
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"

        # Ler arquivo
        file.seek(0)
        file_content = file.read()

        # Upload para Supabase - bucket correto é "image" (não "images")
        bucket_name = "image"
        response = client.storage.from_(bucket_name).upload(
            f"{folder}/{unique_filename}",
            file_content
        )

        # Obter URL pública
        public_url = f"{Config.SUPABASE_URL}/storage/v1/object/public/{bucket_name}/{folder}/{unique_filename}"
        return public_url
    except Exception as e:
        print(f"Erro no upload para Supabase: {e}")
        # Fallback para armazenamento local se falhar
        return None

app = Flask(__name__)
app.config.from_object(Config)
app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH


@app.after_request
def allow_local_frontend_requests(response):
    origin = request.headers.get('Origin')
    allowed_origins = {
        'http://127.0.0.1:5000',
        'http://127.0.0.1:5001',
        'http://localhost:5000',
        'http://localhost:5001',
    }
    if origin in allowed_origins:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Vary'] = 'Origin'
    return response

# SQLAlchemy / Migrations setup
db = SQLAlchemy(app)
migrate = Migrate(app, db)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _ensure_financial_planning_columns():
    with app.app_context():
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if 'financial_planning' not in tables:
            db.create_all()
            return

        existing_columns = {column['name'] for column in inspector.get_columns('financial_planning')}
        missing_columns = {
            'name': 'VARCHAR(200)',
            'observation': 'TEXT',
            'plan_date': 'DATE',
            'is_favorite': 'BOOLEAN DEFAULT 0',
            'organization_notes': 'TEXT',
            'plan_result': 'TEXT',
        }

        for column_name, column_sql in missing_columns.items():
            if column_name not in existing_columns:
                db.session.execute(text(f'ALTER TABLE financial_planning ADD COLUMN {column_name} {column_sql}'))

        db.session.commit()


def init_database():
    with app.app_context():
        db.create_all()
        _ensure_financial_planning_columns()
        migrations_dir = os.path.join(BASE_DIR, 'migrations')
        if os.path.isdir(migrations_dir):
            try:
                upgrade()
            except Exception:
                pass


init_database()


@app.template_filter('from_json')
def from_json_filter(s):
    if s:
        try:
            return json.loads(s)
        except Exception:
            return []
    return []


# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.LargeBinary, nullable=False)
    security_question = db.Column(db.String(200), nullable=False)
    security_answer = db.Column(db.LargeBinary, nullable=False)
    categories_configured = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    email = db.Column(db.String(120), nullable=True)
    profile_photo = db.Column(db.String(255), nullable=True)  # URL da foto de perfil


class Session(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    last_active = db.Column(db.DateTime, default=datetime.now)
    categories_configured = db.Column(db.Boolean, default=False)
    pending_subcategories = db.Column(db.Text)  # JSON string
    selected_category_for_sub = db.Column(db.String(200))
    pending_action = db.Column(db.String(50))
    pending_amount = db.Column(db.Float)
    selected_category = db.Column(db.Integer)
    last_transaction_id = db.Column(db.Integer, db.ForeignKey('transaction.id'))
    pending_intent = db.Column(db.Text)  # JSON string for natural language intent


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    emoji = db.Column(db.String(10), default='📁')
    color = db.Column(db.String(7), default='#3b82f6')
    created_at = db.Column(db.DateTime, default=datetime.now)
    subcategories = db.relationship('Subcategory', backref='category', cascade='all, delete-orphan', lazy=True)
    budgets = db.relationship('Budget', backref='category', cascade='all, delete-orphan', lazy=True)

    def add_subcategory(self, name):
        if not name:
            return None
        normalized = name.strip()
        if not normalized:
            return None
        existing = Subcategory.query.filter_by(category_id=self.id, name=normalized).first()
        if existing:
            return existing
        subcategory = Subcategory(category_id=self.id, name=normalized)
        db.session.add(subcategory)
        return subcategory

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'emoji': self.emoji,
            'color': self.color,
            'subcategories': [subcategory.name for subcategory in self.subcategories]
        }


class Subcategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    subcategory_id = db.Column(db.Integer, db.ForeignKey('subcategory.id'))
    subcategory_name = db.Column(db.String(200))
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(50), nullable=False)  # 'expense' or 'income'
    timestamp = db.Column(db.DateTime, default=datetime.now)


class RecurringTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    subcategory_id = db.Column(db.Integer, db.ForeignKey('subcategory.id'))
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(50), nullable=False)
    interval = db.Column(db.String(20), default='month')
    start_date = db.Column(db.DateTime, default=datetime.now)
    next_run_date = db.Column(db.DateTime, default=datetime.now)
    description = db.Column(db.String(200))


class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    monthly_limit = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


class ChatHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    sender = db.Column(db.String(50), nullable=False)  # 'user' or 'bot'
    timestamp = db.Column(db.DateTime, default=datetime.now)


class MandatoryBill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    due_day = db.Column(db.Integer, nullable=True)  # Dia do mês (1-31), nullable para contas importadas do planejamento
    last_paid_month = db.Column(db.Integer)  # Mês em que foi pago pela última vez
    last_paid_year = db.Column(db.Integer)  # Ano em que foi pago pela última vez
    created_at = db.Column(db.DateTime, default=datetime.now)


class ChecklistHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    bill_id = db.Column(db.Integer, db.ForeignKey('mandatory_bill.id'), nullable=False)
    completed_at = db.Column(db.DateTime, default=datetime.now)
    month = db.Column(db.Integer, nullable=False)  # Mês do checklist
    year = db.Column(db.Integer, nullable=False)  # Ano do checklist
    
    # Relationship
    bill = db.relationship('MandatoryBill', backref='checklist_history')



class WishListItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    total_value = db.Column(db.Float, nullable=False)
    saved_amount = db.Column(db.Float, default=0.0)
    link = db.Column(db.String(500), nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    achieved = db.Column(db.Boolean, default=False)


class FinancialPlanning(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(200), default='Planejamento')
    observation = db.Column(db.Text, default='')
    plan_date = db.Column(db.Date, default=date.today)
    is_favorite = db.Column(db.Boolean, default=False)
    monthly_income = db.Column(db.Float, nullable=False)
    income_type = db.Column(db.String(50), default='fixa')  # fixa, variavel, mista
    other_income = db.Column(db.Float, default=0.0)
    dependents = db.Column(db.Integer, default=0)
    shares_expenses = db.Column(db.Boolean, default=False)

    # Debt information
    has_debts = db.Column(db.Boolean, default=False)
    total_debt = db.Column(db.Float, default=0.0)
    monthly_debt_payment = db.Column(db.Float, default=0.0)
    urgent_debt = db.Column(db.Boolean, default=False)

    # Emergency fund
    emergency_fund_status = db.Column(db.String(50), default='none')  # none, less_1_month, 1_3_months, 3_6_months, more_6_months
    emergency_fund_amount = db.Column(db.Float, default=0.0)
    emergency_fund_goal = db.Column(db.Float, default=0.0)

    # Investment information
    has_investments = db.Column(db.Boolean, default=False)
    investment_amount = db.Column(db.Float, default=0.0)
    monthly_investment = db.Column(db.Float, default=0.0)
    has_retirement = db.Column(db.Boolean, default=False)
    current_age = db.Column(db.Integer, default=30)
    retirement_age = db.Column(db.Integer, default=65)

    # Lifestyle
    lifestyle_budget = db.Column(db.Float, default=0.0)  # Monthly budget for personal enjoyment

    # Dreams and priorities
    priorities = db.Column(db.Text, default='')  # JSON string of priorities
    dreams = db.Column(db.Text, default='')  # JSON string of dream answers
    organization_notes = db.Column(db.Text, default='')
    plan_result = db.Column(db.Text, default='')

    # Calculated distribution
    security_percentage = db.Column(db.Float, default=15.0)
    fixed_expenses_percentage = db.Column(db.Float, default=50.0)
    personal_investment_percentage = db.Column(db.Float, default=10.0)
    travel_percentage = db.Column(db.Float, default=10.0)
    big_goals_percentage = db.Column(db.Float, default=15.0)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class FinancialGoal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    planning_id = db.Column(db.Integer, db.ForeignKey('financial_planning.id'), nullable=True)
    category = db.Column(db.String(50), nullable=False)  # security, fixed_expenses, personal_investment, travel, big_goals
    name = db.Column(db.String(200), nullable=False)
    target_amount = db.Column(db.Float, nullable=False)
    current_amount = db.Column(db.Float, default=0.0)
    monthly_contribution = db.Column(db.Float, default=0.0)
    target_date = db.Column(db.Date, nullable=True)
    priority = db.Column(db.Integer, default=5)  # 1-10
    importance = db.Column(db.Integer, default=5)  # 1-10
    status = db.Column(db.String(50), default='active')  # active, completed, paused
    created_at = db.Column(db.DateTime, default=datetime.now)


class FinancialExpense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    planning_id = db.Column(db.Integer, db.ForeignKey('financial_planning.id'), nullable=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    frequency = db.Column(db.String(50), default='monthly')  # weekly, monthly, yearly
    is_essential = db.Column(db.Boolean, default=False)
    importance = db.Column(db.Integer, default=5)  # 1-10
    can_reduce = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


@login_manager.user_loader
def load_user(user_id):
    init_database()
    return db.session.get(User, int(user_id))


def normalize_text(text):
    if not text:
        return ''
    text = text.lower().strip()
    text = text.replace('€', ' euro ')
    text = text.replace('$', ' dollars ')
    text = text.replace(',', '.')
    return re.sub(r'\s+', ' ', text)


def _get_or_create_user_session(user_id):
    session_record = Session.query.filter_by(user_id=user_id).first()
    if not session_record:
        session_record = Session(user_id=user_id, categories_configured=False)
        db.session.add(session_record)
    return session_record


def _get_user_categories(user_id):
    return Category.query.filter_by(user_id=user_id).order_by(Category.name.asc()).all()


def _parse_plan_date(raw_value):
    if not raw_value:
        return date.today()

    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        try:
            return datetime.strptime(raw_value, '%d/%m/%Y').date()
        except ValueError:
            return date.today()


def _form_to_plan_payload(form):
    def fnum(name, default=0.0):
        try:
            return float(form.get(name) or default)
        except (TypeError, ValueError):
            return float(default)

    def inum(name, default=0):
        try:
            return int(float(form.get(name) or default))
        except (TypeError, ValueError):
            return int(default)

    priorities = []
    for candidate in (form.get('priorities_json') or '', form.get('priorities') or ''):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, list):
            priorities = []
            for item in parsed:
                if isinstance(item, dict):
                    priorities.append(str(item.get('key') or item.get('label') or ''))
                else:
                    priorities.append(str(item))
            priorities = [item for item in priorities if item]
            break

    labels = []
    raw_labels = form.get('priority_labels_json') or ''
    if raw_labels:
        try:
            parsed_labels = json.loads(raw_labels)
            if isinstance(parsed_labels, list):
                labels = [str(item) for item in parsed_labels if str(item).strip()]
        except Exception:
            labels = []

    goals = []
    try:
        parsed_goals = json.loads(form.get('goals_json') or '[]')
        if isinstance(parsed_goals, list):
            goals = parsed_goals
    except Exception:
        goals = []

    names = form.getlist('expense_name[]')
    amounts = form.getlist('expense_amount[]')
    categories = form.getlist('expense_category[]')
    expenses = []
    for index, name in enumerate(names):
        name = (name or '').strip()
        if not name:
            continue
        try:
            amount = float(amounts[index] or 0)
        except (ValueError, IndexError):
            amount = 0
        if amount <= 0:
            continue
        expenses.append({
            'name': name,
            'amount': amount,
            'category': categories[index] if index < len(categories) else '',
        })

    has_debts = form.get('has_debts') == 'on'
    has_investments = form.get('has_investments') == 'on'
    return {
        'monthly_income': fnum('monthly_income'),
        'other_income': fnum('other_income'),
        'income_type': form.get('income_type') or 'fixa',
        'dependents': inum('dependents'),
        'shares_expenses': form.get('shares_expenses') == 'on',
        'has_debts': has_debts,
        'total_debt': fnum('total_debt') if has_debts else 0,
        'monthly_debt_payment': fnum('monthly_debt_payment') if has_debts else 0,
        'urgent_debt': form.get('urgent_debt') == 'on',
        'emergency_fund_status': form.get('emergency_fund_status') or 'none',
        'emergency_fund_amount': fnum('emergency_fund_amount'),
        'has_investments': has_investments,
        'investment_amount': fnum('investment_amount') if has_investments else 0,
        'monthly_investment': fnum('monthly_investment') if has_investments else 0,
        'lifestyle_budget': fnum('lifestyle_budget'),
        'priorities': priorities,
        'priority_labels': labels,
        'dreams': form.get('dreams') or '',
        'organization_notes': form.get('organization_notes') or '',
        'observation': form.get('plan_observation') or '',
        'goals': goals,
        'expenses': expenses,
        'current_age': inum('current_age', 30),
        'retirement_age': inum('retirement_age', 65),
    }


def _payload_from_planning(planning):
    expenses = []
    goals = []
    try:
        for expense in FinancialExpense.query.filter_by(planning_id=planning.id).all():
            expenses.append({
                'name': expense.name,
                'amount': expense.amount,
                'category': expense.category,
            })
        for goal in FinancialGoal.query.filter_by(planning_id=planning.id).all():
            goals.append({
                'name': goal.name,
                'category': goal.category,
                'amount': goal.target_amount,
                'current_amount': goal.current_amount or 0,
                'deadline': 12,
            })
    except Exception:
        pass

    priorities = []
    if planning.priorities:
        try:
            parsed = json.loads(planning.priorities)
            if isinstance(parsed, list):
                priorities = parsed
        except Exception:
            priorities = []

    return {
        # monthly_income já foi gravado como renda total (principal + outras).
        'monthly_income': planning.monthly_income or 0,
        'other_income': 0,
        'income_type': planning.income_type,
        'dependents': planning.dependents or 0,
        'shares_expenses': bool(planning.shares_expenses),
        'has_debts': bool(planning.has_debts),
        'total_debt': planning.total_debt or 0,
        'monthly_debt_payment': planning.monthly_debt_payment or 0,
        'urgent_debt': bool(planning.urgent_debt),
        'emergency_fund_status': planning.emergency_fund_status,
        'emergency_fund_amount': planning.emergency_fund_amount or 0,
        'lifestyle_budget': planning.lifestyle_budget or 0,
        'priorities': priorities,
        'dreams': planning.dreams or '',
        'organization_notes': getattr(planning, 'organization_notes', '') or '',
        'observation': planning.observation or '',
        'goals': goals,
        'expenses': expenses,
        'current_age': planning.current_age or 30,
        'retirement_age': planning.retirement_age or 65,
    }


def _ensure_summary_shape(summary):
    summary = dict(summary or {})
    summary.setdefault('ai_summary', None)
    summary.setdefault('explanations', [])
    summary.setdefault('alerts', [])
    summary.setdefault('boxes', [])
    summary.setdefault('checklist', [])
    summary.setdefault('signals', [])
    summary.setdefault('headline', '')
    summary.setdefault('read_excerpt', '')
    summary.setdefault('selected_priorities', [])
    summary.setdefault('priorities_count', len(summary.get('selected_priorities') or []))
    summary.setdefault('main_priority', '')
    return summary


def _build_planning_summary(planning):
    if not planning:
        return None

    stored_raw = getattr(planning, 'plan_result', None)
    if stored_raw:
        try:
            stored = json.loads(stored_raw)
            if isinstance(stored, dict) and 'security_percentage' in stored:
                return _ensure_summary_shape(stored)
        except Exception:
            pass

    return _ensure_summary_shape(compute_financial_plan(_payload_from_planning(planning)))


def _import_planning_checklist(planning):
    if not planning:
        return False

    try:
        default_category = Category.query.filter_by(user_id=current_user.id, name='Contas Obrigatórias').first()
        if not default_category:
            default_category = Category(
                user_id=current_user.id,
                name='Contas Obrigatórias',
                emoji='📁',
                color='#8b5cf6'
            )
            db.session.add(default_category)
            db.session.flush()

        planning_summary = _build_planning_summary(planning)
        
        # Importar apenas o checklist mensal do planejamento
        security_amount = planning_summary['security_amount'] if planning_summary else 0
        fixed_expenses_amount = planning_summary['fixed_expenses_amount'] if planning_summary else 0
        personal_investment_amount = planning_summary['personal_investment_amount'] if planning_summary else 0
        travel_amount = planning_summary['travel_amount'] if planning_summary else 0
        big_goals_amount = planning_summary['big_goals_amount'] if planning_summary else 0

        # Criar contas obrigatórias com descrição igual ao checklist mensal
        bills_to_create = [
            {'name': f'Separar €{security_amount:.2f} para segurança/futuro', 'amount': security_amount},
            {'name': f'Pagar gastos fixos (€{fixed_expenses_amount:.2f})', 'amount': fixed_expenses_amount},
            {'name': f'Separar €{personal_investment_amount:.2f} para investimento pessoal', 'amount': personal_investment_amount},
            {'name': f'Separar €{travel_amount:.2f} para viagens', 'amount': travel_amount},
            {'name': f'Separar €{big_goals_amount:.2f} para grandes objetivos', 'amount': big_goals_amount},
        ]

        imported = False
        for bill_data in bills_to_create:
            existing_bill = MandatoryBill.query.filter_by(user_id=current_user.id, name=bill_data['name']).first()
            if not existing_bill:
                bill = MandatoryBill(
                    user_id=current_user.id,
                    category_id=default_category.id,
                    name=bill_data['name'],
                    amount=bill_data['amount']
                )
                db.session.add(bill)
                imported = True

        if imported:
            db.session.commit()
        return imported
    except Exception as exc:
        db.session.rollback()
        print(f'Erro ao importar checklist do planejamento: {exc}')
        return False


def _create_categories_from_priorities(user_id, selected_priorities, goals_data, total_income):
    """Create categories based on selected priorities and goals"""
    try:
        import json
        priorities_list = json.loads(selected_priorities) if selected_priorities else []
        
        # Mapping of priorities to category names
        priority_categories = {
            'emergency': 'Reserva de Emergência',
            'retirement': 'Aposentadoria',
            'investments': 'Investimentos',
            'house': 'Compra de Casa',
            'car': 'Compra de Carro',
            'travel': 'Viagens',
            'education': 'Cursos/Faculdade',
            'business': 'Negócio Próprio',
            'wedding': 'Casamento',
            'independence': 'Independência Financeira'
        }
        
        created_categories = []
        
        # Create categories for selected priorities
        for priority in priorities_list:
            if priority in priority_categories:
                existing_cat = Category.query.filter_by(
                    user_id=user_id,
                    name=priority_categories[priority]
                ).first()
                
                if not existing_cat:
                    # Use emojis based on priority
                    emoji_map = {
                        'emergency': '🛡️',
                        'retirement': '👴',
                        'investments': '📈',
                        'house': '🏠',
                        'car': '🚗',
                        'travel': '✈️',
                        'education': '📚',
                        'business': '💼',
                        'wedding': '💒',
                        'independence': '🚀'
                    }
                    
                    new_category = Category(
                        user_id=user_id,
                        name=priority_categories[priority],
                        emoji=emoji_map.get(priority, '📊'),
                        color='#8b5cf6'
                    )
                    db.session.add(new_category)
                    created_categories.append(new_category)
        
        # Create categories for goals if they don't exist
        goal_categories = {
            'technology': 'Tecnologia/Eletrônicos',
            'health': 'Saúde/Tratamentos',
            'family': 'Família/Filhos',
            'hobbies': 'Hobbies/Lazer',
            'home_improvement': 'Reformas/Melhorias',
            'charity': 'Doações/Caridade',
            'emergency_fund': 'Fundo de Emergência'
        }
        
        if goals_data:
            for goal in goals_data:
                if goal in goal_categories:
                    existing_cat = Category.query.filter_by(
                        user_id=user_id,
                        name=goal_categories[goal]
                    ).first()
                    
                    if not existing_cat:
                        emoji_map = {
                            'technology': '💻',
                            'health': '🏥',
                            'family': '👨‍👩‍👧‍👦',
                            'hobbies': '🎨',
                            'home_improvement': '🔧',
                            'charity': '❤️',
                            'emergency_fund': '💰'
                        }
                        
                        new_category = Category(
                            user_id=user_id,
                            name=goal_categories[goal],
                            emoji=emoji_map.get(goal, '📊'),
                            color='#8b5cf6'
                        )
                        db.session.add(new_category)
                        created_categories.append(new_category)
        
        if created_categories:
            db.session.commit()
            print(f'Criadas {len(created_categories)} categorias baseadas nas prioridades: {[c.name for c in created_categories]}')
        
    except Exception as exc:
        db.session.rollback()
        print(f'Erro ao criar categorias das prioridades: {exc}')


def _generate_ai_summary(planning):
    """Generate AI-powered summary of the financial planning using OpenRouter"""
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        api_key = os.getenv('OPENROUTER_API_KEY')
        if not api_key or api_key == 'your_openrouter_api_key_here':
            return None
        
        import openai
        client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        
        # Build the prompt with planning data
        summary_data = {
            'nome': planning.name,
            'renda_mensal': planning.monthly_income,
            'renda_tipo': planning.income_type,
            'outras_rendas': planning.other_income,
            'dependentes': planning.dependents,
            'tem_dividas': planning.has_debts,
            'divida_total': planning.total_debt,
            'pagamento_mensal_divida': planning.monthly_debt_payment,
            'status_reserva_emergencia': planning.emergency_fund_status,
            'valor_reserva_emergencia': planning.emergency_fund_amount,
            'tem_investimentos': planning.has_investments,
            'valor_investimentos': planning.investment_amount,
            'investimento_mensal': planning.monthly_investment,
            'tem_aposentadoria': planning.has_retirement,
            'idade_atual': planning.current_age,
            'idade_aposentadoria': planning.retirement_age,
            'orçamento_lifestyle': planning.lifestyle_budget,
            'distribuicao': {
                'segurança': planning.security_percentage,
                'gastos_fixos': planning.fixed_expenses_percentage,
                'investimento_pessoal': planning.personal_investment_percentage,
                'viagens': planning.travel_percentage,
                'grandes_objetivos': planning.big_goals_percentage
            }
        }
        
        # Parse priorities if available
        if planning.priorities:
            try:
                priorities = json.loads(planning.priorities)
                summary_data['prioridades'] = priorities
            except:
                pass
        
        prompt = f"""
Baseado no seguinte planejamento financeiro, crie um resumo personalizado e explicativo em português:

{json.dumps(summary_data, indent=2, ensure_ascii=False)}

O resumo deve:
1. Resumir os principais pontos do planejamento
2. Explicar o porquê de cada porcentagem de distribuição escolhida
3. Destacar as prioridades selecionadas pelo usuário
4. Explicar como o checklist mensal ajudará a alcançar os objetivos
5. Ser motivador e personalizado

Responda apenas com o texto do resumo, sem formatação adicional.
"""
        
        # Try using OpenRouter's auto model selection
        try:
            response = client.chat.completions.create(
                model="openrouter/free",
                messages=[
                    {"role": "system", "content": "Você é um consultor financeiro especializado em planejamento pessoal."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1000,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as auto_error:
            print(f'Erro com modelo auto: {auto_error}')
            
            # Fallback to specific models
            models_to_try = [
                "meta-llama/llama-3-8b-instruct:free",
                "meta-llama/llama-3-70b-instruct:free",
                "mistralai/mistral-7b-instruct:free"
            ]
            
            for model in models_to_try:
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": "Você é um consultor financeiro especializado em planejamento pessoal."},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=1000,
                        temperature=0.7
                    )
                    return response.choices[0].message.content
                except Exception as model_error:
                    print(f'Erro com modelo {model}: {model_error}')
                    continue
        
        return None
        
    except Exception as exc:
        print(f'Erro ao gerar resumo com IA: {exc}')
        return None


def _resolve_category(user_id, category_name, user_categories=None):
    categories = user_categories or _get_user_categories(user_id)
    if not categories:
        return None
    if not category_name:
        return None
    normalized_target = normalize_text(category_name)
    for category in categories:
        if normalize_text(category.name) == normalized_target:
            return category
    for category in categories:
        if normalized_target in normalize_text(category.name):
            return category
    return None


def _resolve_subcategory(category, text):
    if not category:
        return None
    normalized_text = normalize_text(text)
    for subcategory in category.subcategories:
        if normalize_text(subcategory.name) in normalized_text:
            return subcategory
    return None


def _parse_date_from_text(text):
    normalized = normalize_text(text)
    if 'ontem' in normalized:
        return datetime.now() - timedelta(days=1)
    if 'hoje' in normalized:
        return datetime.now()
    if 'amanha' in normalized or 'amanhã' in normalized:
        return datetime.now() + timedelta(days=1)
    return datetime.now()


def parse_transaction_intent(message, user_id=None, user_categories=None):
    text = (message or '').strip()
    if not text:
        return {'action': 'none'}

    normalized = normalize_text(text)

    if re.search(r'\b(desfazer|cancelar|undo|anular)\b', normalized):
        return {'action': 'undo_last_transaction'}

    api_key = os.getenv('ANTHROPIC_API_KEY')
    if api_key:
        try:
            payload = {
                'model': 'claude-3-5-sonnet-latest',
                'max_tokens': 200,
                'messages': [
                    {
                        'role': 'user',
                        'content': (
                            'Extrai intenção financeira do texto em JSON puro, sem comentários. '
                            'Retorna apenas: {"action":"transaction","type":"expense|income","amount":number,"category_name":"...","subcategory_name":"...","date":"iso|today|yesterday|tomorrow"}. '
                            'Se não conseguires, devolve {"action":"none"}. Texto: ' + text
                        )
                    }
                ]
            }
            response = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': api_key,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json=payload,
                timeout=10,
            )
            if response.ok:
                data = response.json()
                raw_text = ''
                if data.get('content'):
                    for block in data['content']:
                        if block.get('type') == 'text':
                            raw_text += block.get('text', '')
                if raw_text:
                    try:
                        parsed = json.loads(raw_text)
                        if parsed.get('action') == 'transaction':
                            return parsed
                    except Exception:
                        pass
        except Exception:
            pass

    amount_match = re.search(r'(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur|euro|reais|r\$)?', normalized)
    amount = float(amount_match.group(1).replace(',', '.')) if amount_match else None

    tx_type = 'expense'
    if re.search(r'\b(recebi|receber|entrada|renda|salario|salário|deposito|depositar|ganhei)\b', normalized):
        tx_type = 'income'
    elif re.search(r'\b(gastei|gasto|gastar|paguei|paguei|compra|comprei|despesa|remover|subtrair|retirei)\b', normalized):
        tx_type = 'expense'

    category_name = None
    subcategory_name = None
    category_match = re.search(r'\b(?:em|na|de|para)\s+([a-záàâãäçéèêëíìîïóòôõöúùûüñç\s-]+)$', normalized)
    if category_match:
        category_name = category_match.group(1).strip()
    elif re.search(r'\b(?:em|na|de|para)\s+([a-záàâãäçéèêëíìîïóòôõöúùûüñç\s-]+)', normalized):
        category_name = re.search(r'\b(?:em|na|de|para)\s+([a-záàâãäçéèêëíìîïóòôõöúùûüñç\s-]+)', normalized).group(1).strip()

    if not category_name and user_categories is None and user_id is not None:
        user_categories = _get_user_categories(user_id)
    if not category_name and user_categories:
        for category in user_categories:
            if normalize_text(category.name) in normalized:
                category_name = category.name
                break

    if category_name:
        category_name = re.sub(r'\s+', ' ', category_name).strip()
    return {
        'action': 'transaction',
        'type': tx_type,
        'amount': amount,
        'category_name': category_name,
        'subcategory_name': subcategory_name,
        'date': _parse_date_from_text(text),
    }


def create_transaction(user_id, category_id, amount, tx_type, subcategory_name=None, timestamp=None, note=None):
    category = Category.query.get(category_id)
    subcategory = None
    if category and subcategory_name:
        subcategory = _resolve_subcategory(category, subcategory_name)

    transaction = Transaction(
        user_id=user_id,
        category_id=category_id,
        subcategory_id=subcategory.id if subcategory else None,
        subcategory_name=subcategory.name if subcategory else None,
        amount=amount,
        type=tx_type,
        timestamp=timestamp or datetime.now(),
    )
    db.session.add(transaction)
    db.session.flush()
    return transaction


def _create_or_update_budget(user_id, category_id, limit_value):
    budget = Budget.query.filter_by(user_id=user_id, category_id=category_id).first()
    if budget:
        budget.monthly_limit = limit_value
    else:
        budget = Budget(user_id=user_id, category_id=category_id, monthly_limit=limit_value)
        db.session.add(budget)
    db.session.flush()
    return budget


def _get_monthly_spending(user_id, category_id):
    start_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    transactions = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.category_id == category_id,
        Transaction.type == 'expense',
        Transaction.timestamp >= start_of_month,
    ).all()
    return sum(abs(t.amount) for t in transactions)


def _format_budget_progress(category, budget):
    spent = _get_monthly_spending(current_user.id, category.id) if current_user.is_authenticated else 0
    limit = budget.monthly_limit if budget else 0
    if not limit:
        return None
    percentage = min(100, int((spent / limit) * 100)) if limit else 0
    return {'spent': spent, 'limit': limit, 'percentage': percentage}


# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()
        if user and bcrypt.checkpw(password.encode('utf-8'), user.password):
            login_user(user)
            user_session = _get_or_create_user_session(user.id)
            user_session.last_active = datetime.now()
            user_session.categories_configured = user.categories_configured
            db.session.commit()
            return redirect(url_for('dashboard'))
        else:
            flash('Nome de utilizador ou palavra-passe incorretos', 'error')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        security_question = request.form.get('security_question')
        security_answer = request.form.get('security_answer')

        if password != confirm_password:
            flash('As palavras-passe não coincidem', 'error')
            return render_template('register.html')

        if User.query.filter_by(username=username).first():
            flash('Este nome de utilizador já existe', 'error')
            return render_template('register.html')

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        hashed_answer = bcrypt.hashpw(security_answer.lower().encode('utf-8'), bcrypt.gensalt())

        user = User(
            username=username,
            password=hashed_password,
            security_question=security_question,
            security_answer=hashed_answer,
            categories_configured=False
        )

        db.session.add(user)
        db.session.commit()
        flash('Conta criada com sucesso! Por favor, faz login.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/recover', methods=['GET', 'POST'])
def recover():
    if request.method == 'POST':
        username = request.form.get('username')
        security_answer = request.form.get('security_answer')
        new_password = request.form.get('new_password')

        user = User.query.filter_by(username=username).first()
        if not user:
            flash('Utilizador não encontrado', 'error')
            return render_template('recover.html')

        if bcrypt.checkpw(security_answer.lower().encode('utf-8'), user.security_answer):
            hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
            user.password = hashed_password
            db.session.commit()
            flash('Palavra-passe redefinida com sucesso!', 'success')
            return redirect(url_for('login'))
        else:
            flash('Resposta à pergunta de segurança incorreta', 'error')

    return render_template('recover.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        email = request.form.get('email')
        profile_photo = request.form.get('profile_photo')

        # Handle file upload to Supabase
        image_file = request.files.get('image_file')
        if image_file and image_file.filename:
            image_url = upload_image_to_supabase(image_file, 'profile')
            if image_url:
                profile_photo = image_url

        current_user.email = email
        if profile_photo:
            current_user.profile_photo = profile_photo

        db.session.commit()
        flash('Perfil atualizado com sucesso!', 'success')
        return redirect(url_for('profile'))

    return render_template('profile.html', user=current_user)


@app.route('/planning', methods=['GET', 'POST'])
@login_required
def planning():
    if request.method == 'POST':
        try:
            monthly_income = float(request.form.get('monthly_income') or 0)
            income_type = request.form.get('income_type', 'fixa')
            other_income = float(request.form.get('other_income') or 0)
            dependents = int(request.form.get('dependents') or 0)
            shares_expenses = request.form.get('shares_expenses') == 'on'

            has_debts = request.form.get('has_debts') == 'on'
            total_debt = float(request.form.get('total_debt') or 0) if has_debts else 0
            monthly_debt_payment = float(request.form.get('monthly_debt_payment') or 0) if has_debts else 0
            urgent_debt = request.form.get('urgent_debt') == 'on'

            emergency_fund_status = request.form.get('emergency_fund_status', 'none')
            emergency_fund_amount = float(request.form.get('emergency_fund_amount') or 0)

            has_investments = request.form.get('has_investments') == 'on'
            investment_amount = float(request.form.get('investment_amount') or 0) if has_investments else 0
            monthly_investment = float(request.form.get('monthly_investment') or 0) if has_investments else 0
            has_retirement = request.form.get('has_retirement') == 'on'
            current_age = int(request.form.get('current_age') or 30)
            retirement_age = int(request.form.get('retirement_age') or 65)

            lifestyle_budget = float(request.form.get('lifestyle_budget') or 0)
            priorities = request.form.get('priorities', '')
            dreams = request.form.get('dreams', '')
            plan_name = (request.form.get('plan_name') or '').strip() or f'Planejamento {datetime.now().strftime("%d/%m/%Y")}'
            plan_observation = request.form.get('plan_observation', '').strip()
            plan_date = _parse_plan_date(request.form.get('plan_date'))
            save_as_favorite = request.form.get('save_as_favorite') == 'on'
            add_mandatory_bills = request.form.get('add_mandatory_bills') == 'on'

            total_income = monthly_income + other_income
            payload = _form_to_plan_payload(request.form)
            summary = compute_financial_plan(payload)
            selected_priorities = payload['priorities']
            goals_data = [goal.get('category') for goal in payload['goals'] if isinstance(goal, dict)]
            security_percentage = summary['security_percentage']
            fixed_expenses_percentage = summary['fixed_expenses_percentage']
            personal_investment_percentage = summary['personal_investment_percentage']
            travel_percentage = summary['travel_percentage']
            big_goals_percentage = summary['big_goals_percentage']
            if summary.get('total_income'):
                total_income = summary['total_income']
            priorities = json.dumps(selected_priorities, ensure_ascii=False)
            dreams = payload['dreams']

            _create_categories_from_priorities(
                current_user.id,
                json.dumps(selected_priorities, ensure_ascii=False),
                goals_data,
                total_income,
            )

            if save_as_favorite:
                FinancialPlanning.query.filter_by(user_id=current_user.id).update({'is_favorite': False})

            planning = FinancialPlanning(
                user_id=current_user.id,
                name=plan_name,
                observation=plan_observation,
                plan_date=plan_date,
                is_favorite=save_as_favorite,
                monthly_income=total_income,
                income_type=income_type,
                other_income=other_income,
                dependents=dependents,
                shares_expenses=shares_expenses,
                has_debts=has_debts,
                total_debt=total_debt,
                monthly_debt_payment=monthly_debt_payment,
                urgent_debt=urgent_debt,
                emergency_fund_status=emergency_fund_status,
                emergency_fund_amount=emergency_fund_amount,
                has_investments=has_investments,
                investment_amount=investment_amount,
                monthly_investment=monthly_investment,
                has_retirement=has_retirement,
                current_age=current_age,
                retirement_age=retirement_age,
                lifestyle_budget=lifestyle_budget,
                priorities=priorities,
                dreams=dreams,
                organization_notes=payload['organization_notes'],
                plan_result=json.dumps(summary, ensure_ascii=False),
                security_percentage=security_percentage,
                fixed_expenses_percentage=fixed_expenses_percentage,
                personal_investment_percentage=personal_investment_percentage,
                travel_percentage=travel_percentage,
                big_goals_percentage=big_goals_percentage,
            )

            db.session.add(planning)
            db.session.flush()
            for expense in payload['expenses']:
                db.session.add(FinancialExpense(
                    user_id=current_user.id,
                    planning_id=planning.id,
                    name=expense['name'],
                    category=expense.get('category') or 'outros',
                    amount=expense['amount'],
                ))
            for goal in summary.get('goals') or []:
                db.session.add(FinancialGoal(
                    user_id=current_user.id,
                    planning_id=planning.id,
                    category=goal.get('bucket') or goal.get('category') or 'big_goals',
                    name=goal['name'],
                    target_amount=goal.get('amount') or 0,
                    current_amount=goal.get('current') or 0,
                    monthly_contribution=goal.get('monthly') or 0,
                ))
            db.session.commit()

            # Create mandatory bills if requested
            if add_mandatory_bills:
                try:
                    from app import MandatoryBill, Category
                    default_category = Category.query.filter_by(user_id=current_user.id, name='Contas Obrigatórias').first()
                    if not default_category:
                        default_category = Category(
                            user_id=current_user.id,
                            name='Contas Obrigatórias',
                            emoji='📁',
                            color='#8b5cf6'
                        )
                        db.session.add(default_category)
                        db.session.commit()

                    # Create bills based on fixed expenses percentage
                    fixed_expenses_amount = total_income * (fixed_expenses_percentage / 100)

                    # Create a few sample bills
                    bills_to_create = [
                        {'name': 'Aluguel/Moradia', 'amount': fixed_expenses_amount * 0.4, 'due_day': 5},
                        {'name': 'Contas (Água/Luz/Gás)', 'amount': fixed_expenses_amount * 0.2, 'due_day': 10},
                        {'name': 'Internet/Telefone', 'amount': fixed_expenses_amount * 0.1, 'due_day': 15},
                        {'name': 'Supermercado', 'amount': fixed_expenses_amount * 0.3, 'due_day': 20},
                    ]

                    for bill_data in bills_to_create:
                        existing_bill = MandatoryBill.query.filter_by(
                            user_id=current_user.id,
                            name=bill_data['name']
                        ).first()

                        if not existing_bill:
                            bill = MandatoryBill(
                                user_id=current_user.id,
                                category_id=default_category.id,
                                name=bill_data['name'],
                                amount=bill_data['amount'],
                                due_day=bill_data['due_day']
                            )
                            db.session.add(bill)

                    db.session.commit()
                    flash('Planejamento salvo e contas obrigatórias criadas!', 'success')
                except Exception as e:
                    db.session.rollback()
                    print(f'Erro ao criar contas obrigatórias: {e}')
                    flash('Planejamento salvo, mas houve erro ao criar contas obrigatórias.', 'warning')
            else:
                flash('Planejamento salvo com sucesso!', 'success')

            if request.form.get('as_json') == '1':
                return jsonify({'success': True, 'id': planning.id, 'planning_data': summary})
            return redirect(url_for('planning'))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao criar planejamento: {str(e)}', 'error')
            return redirect(url_for('planning'))

    planning_items = FinancialPlanning.query.filter_by(user_id=current_user.id).order_by(
        FinancialPlanning.is_favorite.desc(),
        FinancialPlanning.plan_date.desc(),
        FinancialPlanning.created_at.desc(),
        FinancialPlanning.id.desc()
    ).all()

    if planning_items and not any(item.is_favorite for item in planning_items):
        planning_items[0].is_favorite = True
        db.session.commit()
        planning_items = FinancialPlanning.query.filter_by(user_id=current_user.id).order_by(
            FinancialPlanning.is_favorite.desc(),
            FinancialPlanning.plan_date.desc(),
            FinancialPlanning.created_at.desc(),
            FinancialPlanning.id.desc()
        ).all()

    favorite_plan = planning_items[0] if planning_items else None
    planning_history = [item for item in planning_items if not item.is_favorite] if planning_items else []

    planning_data = _build_planning_summary(favorite_plan) if favorite_plan else None

    return render_template(
        'planning.html',
        planning=favorite_plan,
        planning_data=planning_data,
        favorite_plan=favorite_plan,
        planning_history=planning_history,
        all_plans=planning_items,
        today_date=date.today()
    )


@app.route('/planning/favorite/<int:planning_id>', methods=['POST'])
@login_required
def set_favorite_planning(planning_id):
    try:
        # Remove favorite from all user's plans
        FinancialPlanning.query.filter_by(user_id=current_user.id).update({'is_favorite': False})

        # Set new favorite
        planning = FinancialPlanning.query.filter_by(id=planning_id, user_id=current_user.id).first()
        if planning:
            planning.is_favorite = True
            db.session.commit()
            flash('Planejamento definido como favorito!', 'success')
        else:
            flash('Planejamento não encontrado', 'error')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao definir favorito: {str(e)}', 'error')

    return redirect(url_for('planning'))


@app.route('/planning/view/<int:planning_id>')
@login_required
def view_planning(planning_id):
    planning = FinancialPlanning.query.filter_by(id=planning_id, user_id=current_user.id).first()
    if not planning:
        flash('Planejamento não encontrado', 'error')
        return redirect(url_for('planning'))

    planning_data = _build_planning_summary(planning)

    return render_template(
        'planning.html',
        planning=planning,
        planning_data=planning_data,
        favorite_plan=planning,
        planning_history=[],
        all_plans=FinancialPlanning.query.filter_by(user_id=current_user.id).all(),
        today_date=date.today()
    )


@app.route('/planning/detail/<int:planning_id>')
@login_required
def planning_detail(planning_id):
    planning = FinancialPlanning.query.filter_by(id=planning_id, user_id=current_user.id).first()
    if not planning:
        return jsonify({'error': 'Planejamento não encontrado'}), 404

    planning_data = _build_planning_summary(planning)
    checklist = planning_data.get('checklist') or []
    return jsonify({
        'id': planning.id,
        'name': planning.name,
        'observation': planning.observation,
        'plan_date': planning.plan_date.isoformat() if planning.plan_date else None,
        'is_favorite': planning.is_favorite,
        'planning_data': planning_data,
        'checklist': [{'label': item, 'completed': False} for item in checklist],
    })


@app.route('/planning/new')
@login_required
def new_planning():
    return redirect(url_for('planning'))


@app.route('/planning/import-checklist/<int:planning_id>', methods=['POST'])
@login_required
def import_planning_checklist(planning_id):
    planning = FinancialPlanning.query.filter_by(id=planning_id, user_id=current_user.id).first()
    if not planning:
        return jsonify({'error': 'Planejamento não encontrado'}), 404

    imported = _import_planning_checklist(planning)
    if imported:
        return jsonify({'success': True, 'redirect': url_for('dashboard', imported_from_planning=planning_id)})
    else:
        return jsonify({'error': 'Não foi possível importar o checklist'}), 500






@app.route('/api/planning/preview', methods=['POST'])
@login_required
def preview_planning():
    payload = _form_to_plan_payload(request.form)
    summary = compute_financial_plan(payload)
    return jsonify({'success': True, 'planning_data': summary})


@app.route('/wishlist', methods=['GET', 'POST'])
@login_required
def wishlist():
    if request.method == 'POST':
        print("=== DEBUG: Formulário POST recebido ===")
        print(f"Content-Type: {request.content_type}")
        print(f"Form data: {request.form}")
        print(f"Files: {request.files}")

        name = request.form.get('name')
        total_value = request.form.get('total_value')
        link = request.form.get('link')
        image_url = request.form.get('image_url')
        category_id = request.form.get('category_id')

        print(f"Nome: {name}, Valor: {total_value}, Link: {link}, Imagem URL: {image_url}, Categoria: {category_id}")

        # Handle file upload to Supabase
        image_file = request.files.get('image_file')
        print(f"Arquivo de imagem: {image_file}")
        if image_file:
            print(f"Nome do arquivo: {image_file.filename}")
            print(f"Tipo de conteúdo: {image_file.content_type}")

        if image_file and image_file.filename:
            uploaded_url = upload_image_to_supabase(image_file, 'wishlist')
            if uploaded_url:
                image_url = uploaded_url
                print(f"Upload Supabase sucesso: {image_url}")
            else:
                # Fallback para armazenamento local
                filename = secure_filename(image_file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                unique_filename = f"{timestamp}_{filename}"
                image_file.save(os.path.join(BASE_DIR, 'uploads', unique_filename))
                image_url = f"/uploads/{unique_filename}"
                print(f"Upload local sucesso: {image_url}")

        if name and total_value:
            wishlist_item = WishListItem(
                user_id=current_user.id,
                name=name,
                total_value=float(total_value),
                link=link,
                image_url=image_url,
                category_id=int(category_id) if category_id else None
            )
            db.session.add(wishlist_item)
            db.session.commit()
            print(f"Item salvo com image_url: {wishlist_item.image_url}")
            flash('Item adicionado à Wish List!', 'success')
            return redirect(url_for('wishlist'))

    wishlist_items = WishListItem.query.filter_by(user_id=current_user.id).all()
    user_categories = _get_user_categories(current_user.id)
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()

    # Calcular category_totals para mostrar saldos no modal
    category_totals = {}
    for cat in user_categories:
        cat_expense = sum(t.amount for t in transactions if t.category_id == cat.id and t.type == 'expense')
        cat_income = sum(t.amount for t in transactions if t.category_id == cat.id and t.type == 'income')
        category_totals[cat.id] = {'expense': cat_expense, 'income': cat_income, 'net': cat_income - cat_expense}

    return render_template('wishlist.html', wishlist_items=wishlist_items, categories=user_categories, category_totals=category_totals)


@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'uploads'), filename)


@app.route('/dashboard')
@login_required
def dashboard():
    import_planning_id = request.args.get('imported_from_planning')
    if import_planning_id:
        planning = FinancialPlanning.query.filter_by(id=int(import_planning_id), user_id=current_user.id).first()
        if planning:
            _import_planning_checklist(planning)
            flash('Checklist importado do planejamento com sucesso!', 'success')

    user_categories = _get_user_categories(current_user.id)
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()

    category_names = {cat.id: cat.name for cat in user_categories}

    category_totals = {}
    total_spent = 0
    total_income = 0
    for cat in user_categories:
        cat_expense = sum(t.amount for t in transactions if t.category_id == cat.id and t.type == 'expense')
        cat_income = sum(t.amount for t in transactions if t.category_id == cat.id and t.type == 'income')
        category_totals[cat.id] = {'expense': cat_expense, 'income': cat_income, 'net': cat_income - cat_expense}
        total_spent += cat_expense
        total_income += cat_income

    highest_spending = None
    max_amount = 0
    for cat in user_categories:
        cat_expense = category_totals.get(cat.id, {}).get('expense', 0)
        if cat_expense > max_amount:
            max_amount = cat_expense
            highest_spending = cat

    budgets = {
        category.id: Budget.query.filter_by(user_id=current_user.id, category_id=category.id).first()
        for category in user_categories
    }

    # Contas obrigatórias
    mandatory_bills = MandatoryBill.query.filter_by(user_id=current_user.id).all()

    # Calcular contas pendentes
    current_month = datetime.now().month
    current_year = datetime.now().year
    pending_bills_count = sum(1 for bill in mandatory_bills
                             if not (bill.last_paid_month == current_month and bill.last_paid_year == current_year))

    # Wish List items
    wishlist_items = WishListItem.query.filter_by(user_id=current_user.id).all()
    wishlist_count = len(wishlist_items)

    return render_template(
        'dashboard.html',
        categories=user_categories,
        transactions=transactions,
        category_totals=category_totals,
        total_spent=total_spent,
        total_income=total_income,
        highest_spending=highest_spending,
        category_names=category_names,
        categories_json=[{
            'id': cat.id,
            'name': cat.name,
            'emoji': cat.emoji,
            'color': cat.color,
            'subcategories': [sub.name for sub in cat.subcategories]
        } for cat in user_categories],
        budgets=budgets,
        mandatory_bills=mandatory_bills,
        pending_bills_count=pending_bills_count,
        wishlist_count=wishlist_count,
    )


@app.route('/chat')
@login_required
def chat():
    user_session = Session.query.filter_by(user_id=current_user.id).first()
    user_categories = _get_user_categories(current_user.id)
    chat_history = ChatHistory.query.filter_by(user_id=current_user.id).order_by(ChatHistory.timestamp.asc()).all()

    return render_template(
        'chat.html',
        session=user_session,
        categories=user_categories,
        chat_history=chat_history,
    )


@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    data = request.json or {}
    user_message = (data.get('message') or '').strip()

    chat_msg = ChatHistory(user_id=current_user.id, message=user_message, sender='user', timestamp=datetime.now())
    db.session.add(chat_msg)

    result = process_chat_message(user_message, current_user.id)
    response_text = result['text'] if isinstance(result, dict) else str(result)
    suggestions = result.get('suggestions', []) if isinstance(result, dict) else []
    balances = result.get('balances') if isinstance(result, dict) else None
    if balances is None:
        balances = _category_balances(current_user.id)

    bot_msg = ChatHistory(user_id=current_user.id, message=response_text, sender='bot', timestamp=datetime.now())
    db.session.add(bot_msg)
    db.session.commit()

    return jsonify({
        'response': response_text,
        'suggestions': suggestions,
        'balances': balances,
        'saved': result.get('saved') if isinstance(result, dict) else None,
    })


@app.route('/api/categories', methods=['GET'])
@login_required
def api_categories():
    categories = _get_user_categories(current_user.id)
    return jsonify([cat.to_dict() for cat in categories])


@app.route('/api/category', methods=['POST'])
@login_required
def api_add_category():
    data = request.get_json() or {}
    name = data.get('name')
    emoji = data.get('emoji', '📁')
    color = data.get('color', '#3b82f6')
    subcategories = data.get('subcategories', []) or []

    if not name:
        return jsonify({'error': 'Name is required'}), 400

    existing = Category.query.filter_by(user_id=current_user.id, name=name).first()
    if existing:
        return jsonify({'error': 'Category already exists'}), 400

    if len(subcategories) > 10:
        return jsonify({'error': 'Maximum 10 subcategories allowed'}), 400

    category = Category(user_id=current_user.id, name=name, emoji=emoji, color=color)
    db.session.add(category)
    db.session.flush()

    for subcategory_name in subcategories:
        if subcategory_name:
            category.add_subcategory(subcategory_name)

    user_session = _get_or_create_user_session(current_user.id)
    user_session.categories_configured = True
    db.session.commit()

    return jsonify({'success': True, 'id': category.id})


@app.route('/api/category/<int:category_id>/check', methods=['GET'])
@login_required
def api_check_category(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404

    transaction = Transaction.query.filter_by(category_id=category_id).first()
    has_transactions = transaction is not None
    return jsonify({'has_transactions': has_transactions})


@app.route('/api/category/<int:category_id>/move', methods=['POST'])
@login_required
def api_move_transactions(category_id):
    data = request.get_json() or {}
    target_id = data.get('target_id')

    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404

    target = Category.query.get(target_id)
    if not target or target.user_id != current_user.id:
        return jsonify({'error': 'Target category not found'}), 404

    transactions = Transaction.query.filter_by(category_id=category_id).all()
    for transaction in transactions:
        transaction.category_id = target_id

    db.session.delete(category)
    db.session.commit()

    return jsonify({'success': True})


@app.route('/api/category/<int:category_id>', methods=['GET'])
@login_required
def api_get_category(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404

    return jsonify(category.to_dict())


@app.route('/api/category/<int:category_id>', methods=['PUT'])
@login_required
def api_update_category(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404

    data = request.get_json() or {}
    name = data.get('name')
    emoji = data.get('emoji', '📁')
    color = data.get('color', '#3b82f6')
    subcategories = data.get('subcategories')

    if not name:
        return jsonify({'error': 'Name is required'}), 400

    existing = Category.query.filter_by(user_id=current_user.id, name=name).first()
    if existing and existing.id != category_id:
        return jsonify({'error': 'Category name already exists'}), 400

    if subcategories is not None and len(subcategories) > 10:
        return jsonify({'error': 'Maximum 10 subcategories allowed'}), 400

    category.name = name
    category.emoji = emoji
    category.color = color
    if subcategories is not None:
        existing_subcategories = list(category.subcategories)
        for item in existing_subcategories:
            db.session.delete(item)
        for subcategory_name in subcategories:
            if subcategory_name:
                category.add_subcategory(subcategory_name)

    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/category/<int:category_id>', methods=['DELETE'])
@login_required
def api_delete_category(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404

    db.session.delete(category)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/category/<int:category_id>/subcategory', methods=['POST'])
@login_required
def api_add_subcategory(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404

    data = request.get_json() or {}
    name = data.get('name')
    if not name:
        return jsonify({'error': 'Name is required'}), 400

    if len(category.subcategories) >= 10:
        return jsonify({'error': 'Maximum 10 subcategories allowed'}), 400

    if any(sub.name == name for sub in category.subcategories):
        return jsonify({'error': 'Subcategory already exists'}), 400

    category.add_subcategory(name)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/category/<int:category_id>/subcategory', methods=['DELETE'])
@login_required
def api_delete_subcategory(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404

    data = request.get_json() or {}
    index = data.get('index')
    if index is None:
        return jsonify({'error': 'Index is required'}), 400

    subcategories = list(category.subcategories)
    if 0 <= index < len(subcategories):
        db.session.delete(subcategories[index])
        db.session.commit()

    return jsonify({'success': True})


@app.route('/api/category/<int:category_id>/subcategory-totals', methods=['GET'])
@login_required
def api_subcategory_totals(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404

    transactions = Transaction.query.filter_by(category_id=category_id).all()

    totals = {sub.name: 0 for sub in category.subcategories}
    no_subcategory_total = 0
    for transaction in transactions:
        if transaction.subcategory_id and transaction.subcategory_name:
            if transaction.subcategory_name in totals:
                totals[transaction.subcategory_name] += abs(transaction.amount)
            else:
                no_subcategory_total += abs(transaction.amount)
        else:
            no_subcategory_total += abs(transaction.amount)

    return jsonify({
        'category_id': category.id,
        'category_name': category.name,
        'category_color': category.color,
        'subcategories': [{'name': sub_name, 'total': totals[sub_name]} for sub_name in totals],
        'no_subcategory_total': no_subcategory_total,
    })


@app.route('/api/dashboard-data', methods=['GET'])
@login_required
def api_dashboard_data():
    user_categories = _get_user_categories(current_user.id)
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()

    category_totals = {}
    for cat in user_categories:
        cat_total = sum(abs(t.amount) for t in transactions if t.category_id == cat.id and t.type == 'expense')
        category_totals[cat.name] = cat_total

    return jsonify({
        'categories': [cat.name for cat in user_categories],
        'totals': category_totals,
        'total_spent': sum(category_totals.values())
    })


@app.route('/api/budget', methods=['POST'])
@login_required
def api_set_budget():
    data = request.get_json() or {}
    category_id = data.get('category_id')
    monthly_limit = data.get('monthly_limit')
    if not category_id or monthly_limit is None:
        return jsonify({'error': 'Invalid payload'}), 400

    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404

    budget = _create_or_update_budget(current_user.id, category.id, float(monthly_limit))
    return jsonify({'success': True, 'budget': {'category_id': category.id, 'monthly_limit': budget.monthly_limit}})


# Mandatory Bills API Routes
@app.route('/api/mandatory-bill', methods=['POST'])
@login_required
def api_create_mandatory_bill():
    data = request.get_json() or {}
    name = data.get('name')
    amount = data.get('amount')
    category_id = data.get('category_id')
    due_day = data.get('due_day')

    if not all([name, amount, category_id, due_day]):
        return jsonify({'success': False, 'error': 'Todos os campos são obrigatórios'}), 400

    try:
        bill = MandatoryBill(
            user_id=current_user.id,
            category_id=category_id,
            name=name,
            amount=float(amount),
            due_day=int(due_day)
        )
        db.session.add(bill)
        db.session.commit()
        return jsonify({'success': True, 'bill_id': bill.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mandatory-bill/<int:bill_id>', methods=['DELETE'])
@login_required
def api_delete_mandatory_bill(bill_id):
    bill = MandatoryBill.query.filter_by(id=bill_id, user_id=current_user.id).first()
    if not bill:
        return jsonify({'success': False, 'error': 'Conta não encontrada'}), 404

    try:
        db.session.delete(bill)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mandatory-bill/payment', methods=['POST'])
@login_required
def api_update_bill_payment():
    data = request.get_json() or {}
    bill_id = data.get('bill_id')
    is_paid = data.get('is_paid')
    month = data.get('month')
    year = data.get('year')

    if not all([bill_id, is_paid is not None, month, year]):
        return jsonify({'success': False, 'error': 'Dados incompletos'}), 400

    bill = MandatoryBill.query.filter_by(id=bill_id, user_id=current_user.id).first()
    if not bill:
        return jsonify({'success': False, 'error': 'Conta não encontrada'}), 404

    try:
        if is_paid:
            bill.last_paid_month = month
            bill.last_paid_year = year
            
            # Add to checklist history
            history = ChecklistHistory(
                user_id=current_user.id,
                bill_id=bill.id,
                month=month,
                year=year
            )
            db.session.add(history)
        else:
            bill.last_paid_month = None
            bill.last_paid_year = None
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# Wish List API Routes
@app.route('/api/wishlist/<int:item_id>', methods=['DELETE'])
@login_required
def api_delete_wishlist_item(item_id):
    item = WishListItem.query.filter_by(id=item_id, user_id=current_user.id).first()
    if not item:
        return jsonify({'success': False, 'error': 'Item não encontrado'}), 404

    try:
        db.session.delete(item)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/wishlist/<int:item_id>/add-funds', methods=['POST'])
@login_required
def api_add_wishlist_funds(item_id):
    data = request.get_json() or {}
    amount = data.get('amount')
    category_id = data.get('category_id')

    if not all([amount, category_id]):
        return jsonify({'success': False, 'error': 'Dados incompletos'}), 400

    item = WishListItem.query.filter_by(id=item_id, user_id=current_user.id).first()
    if not item:
        return jsonify({'success': False, 'error': 'Item não encontrado'}), 404

    category = Category.query.filter_by(id=category_id, user_id=current_user.id).first()
    if not category:
        return jsonify({'success': False, 'error': 'Categoria não encontrada'}), 404

    try:
        # Verificar se a categoria tem saldo suficiente
        category_transactions = Transaction.query.filter_by(
            user_id=current_user.id,
            category_id=category_id
        ).all()

        cat_income = sum(t.amount for t in category_transactions if t.type == 'income')
        cat_expense = sum(t.amount for t in category_transactions if t.type == 'expense')
        category_balance = cat_income - cat_expense

        if category_balance < amount:
            return jsonify({'success': False, 'error': f'A categoria {category.name} não tem saldo suficiente. Saldo atual: €{category_balance:.2f}'}), 400

        # Remover dinheiro da categoria
        transaction = Transaction(
            user_id=current_user.id,
            category_id=category_id,
            amount=amount,
            type='expense',
            subcategory_name='Wish List'
        )
        db.session.add(transaction)

        # Adicionar à Wish List
        item.saved_amount += amount

        # Verificar se foi conquistado
        if item.saved_amount >= item.total_value:
            item.achieved = True

        db.session.commit()
        return jsonify({'success': True, 'new_saved_amount': item.saved_amount, 'achieved': item.achieved})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/wishlist/<int:item_id>/return-funds', methods=['POST'])
@login_required
def api_return_wishlist_funds(item_id):
    data = request.get_json() or {}
    category_id = data.get('category_id')

    if not category_id:
        return jsonify({'success': False, 'error': 'Categoria não especificada'}), 400

    item = WishListItem.query.filter_by(id=item_id, user_id=current_user.id).first()
    if not item:
        return jsonify({'success': False, 'error': 'Item não encontrado'}), 404

    category = Category.query.filter_by(id=category_id, user_id=current_user.id).first()
    if not category:
        return jsonify({'success': False, 'error': 'Categoria não encontrada'}), 404

    try:
        # Devolver dinheiro para a categoria
        if item.saved_amount > 0:
            transaction = Transaction(
                user_id=current_user.id,
                category_id=category_id,
                amount=item.saved_amount,
                type='income',
                subcategory_name='Devolução Wish List'
            )
            db.session.add(transaction)

        # Remover o item
        db.session.delete(item)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/wishlist/<int:item_id>', methods=['PUT'])
@login_required
def api_update_wishlist_item(item_id):
    # Handle both JSON and FormData
    if request.content_type and 'multipart/form-data' in request.content_type:
        data = request.form.to_dict()
        image_file = request.files.get('image_file')
    else:
        data = request.get_json() or {}
        image_file = None

    name = data.get('name')
    total_value = data.get('total_value')
    link = data.get('link')
    image_url = data.get('image_url')
    category_id = data.get('category_id')

    item = WishListItem.query.filter_by(id=item_id, user_id=current_user.id).first()
    if not item:
        return jsonify({'success': False, 'error': 'Item não encontrado'}), 404

    try:
        if name:
            item.name = name
        if total_value:
            item.total_value = float(total_value)
        if link is not None:
            item.link = link
        if image_url is not None:
            item.image_url = image_url
        if category_id is not None:
            item.category_id = int(category_id) if category_id else None

        # Handle file upload if present
        if image_file and image_file.filename:
            uploaded_url = upload_image_to_supabase(image_file, 'wishlist')
            if uploaded_url:
                item.image_url = uploaded_url
            else:
                # Fallback para armazenamento local
                filename = secure_filename(image_file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                unique_filename = f"{timestamp}_{filename}"
                image_file.save(os.path.join(BASE_DIR, 'uploads', unique_filename))
                item.image_url = f"/uploads/{unique_filename}"

        # Recalcular achieved status
        if item.saved_amount >= item.total_value:
            item.achieved = True
        else:
            item.achieved = False

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.context_processor
def inject_now():
    return {'now': datetime.now}
    
CHAT_HELP_MESSAGE = (
    "Oi! Pode falar do jeito que você anota no caderno.\n\n"
    "• recebi 2500 de salário\n"
    "• gastei 45 no mercado\n"
    "• paguei 25 de transporte\n"
    "• recebi 2500 de salário e gastei 80 no mercado\n\n"
    "Eu mostro o que entendi e só gravo quando você confirma.\n"
    "Para apagar o último lançamento, escreva desfazer."
)

QUICK_SUGGESTIONS = [
    {'label': 'Recebi salário', 'message': 'Recebi 2500 de salário'},
    {'label': 'Mercado', 'message': 'Gastei 80 no mercado'},
    {'label': 'Transporte', 'message': 'Paguei 25 de transporte'},
    {'label': 'Café', 'message': 'Gastei 18 no café'},
    {'label': 'Desfazer', 'message': 'desfazer'},
]

CONFIRM_SUGGESTIONS = [
    {'label': 'Confirmar', 'message': 'sim', 'variant': 'primary'},
    {'label': 'Não registrar', 'message': 'não', 'variant': 'ghost'},
]

CATEGORY_COLORS = ['#8b5cf6', '#ec4899', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#6366f1']


def _money(amount):
    return f'€{float(amount):.2f}'


def _pretty_category(name):
    return ' '.join(part[:1].upper() + part[1:] for part in (name or '').split() if part)


def _emoji_for(name, tx_type):
    folded = fold_chat(name)
    table = (
        ('salario', '💰'), ('mercado', '🛒'), ('aliment', '🍽️'), ('cafe', '☕'),
        ('transporte', '🚌'), ('uber', '🚗'), ('aluguel', '🏠'), ('moradia', '🏠'),
        ('lazer', '🎉'), ('saude', '🏥'), ('curso', '📚'), ('viag', '✈️'),
        ('luz', '💡'), ('internet', '📶'),
    )
    for key, emoji in table:
        if key in folded:
            return emoji
    return '💰' if tx_type == 'income' else '💸'


def _category_balances(user_id):
    categories = _get_user_categories(user_id)
    transactions = Transaction.query.filter_by(user_id=user_id).all()
    balances = []
    for category in categories:
        income = sum(item.amount for item in transactions if item.category_id == category.id and item.type == 'income')
        expense = sum(item.amount for item in transactions if item.category_id == category.id and item.type == 'expense')
        balances.append({
            'name': category.name,
            'emoji': category.emoji or '📁',
            'color': category.color or '#8b5cf6',
            'balance': round(income - expense, 2),
        })
    return balances


def _load_pending(user_session):
    if not user_session or not user_session.pending_intent:
        return {}
    try:
        pending = json.loads(user_session.pending_intent)
    except Exception:
        return {}
    return pending if isinstance(pending, dict) else {}


def _save_pending(user_session, pending):
    user_session.pending_intent = json.dumps(pending, ensure_ascii=False) if pending else None
    user_session.pending_action = None
    user_session.pending_amount = None
    user_session.selected_category = None


def _chat_result(text, suggestions=None, saved=None, user_id=None):
    return {
        'text': text,
        'suggestions': suggestions if suggestions is not None else QUICK_SUGGESTIONS,
        'saved': saved,
        'balances': _category_balances(user_id) if user_id else [],
    }


def _resolve_category_loose(user_id, category_name):
    target = fold_chat(category_name)
    if not target:
        return None
    for category in _get_user_categories(user_id):
        folded = fold_chat(category.name)
        if folded == target or target in folded or folded in target:
            return category
    return None


def _get_or_create_category(user_id, name, tx_type):
    existing = _resolve_category_loose(user_id, name)
    if existing:
        return existing
    pretty = _pretty_category(name)
    color = CATEGORY_COLORS[len(_get_user_categories(user_id)) % len(CATEGORY_COLORS)]
    category = Category(
        user_id=user_id,
        name=pretty,
        emoji=_emoji_for(name, tx_type),
        color=color,
    )
    db.session.add(category)
    db.session.flush()
    user_session = _get_or_create_user_session(user_id)
    user_session.categories_configured = True
    user = db.session.get(User, user_id)
    if user:
        user.categories_configured = True
    return category


def _timestamp_from_hint(hint):
    if hint == 'yesterday':
        return datetime.now() - timedelta(days=1)
    if hint == 'tomorrow':
        return datetime.now() + timedelta(days=1)
    return datetime.now()


def _describe_transactions(transactions):
    lines = []
    for index, tx in enumerate(transactions, start=1):
        kind = 'Receita' if tx.get('type') == 'income' else 'Despesa'
        category = _pretty_category(tx.get('category') or '') or 'sem categoria'
        lines.append(f'{index}. {kind} de {_money(tx.get("amount") or 0)} em {category}')
    return '\n'.join(lines)


def _commit_chat_transactions(user_id, transactions):
    saved = []
    for tx in transactions:
        if not tx.get('category'):
            continue
        category = _get_or_create_category(user_id, tx['category'], tx.get('type') or 'expense')
        subcategory = _resolve_subcategory(category, tx.get('category') or '')
        transaction = Transaction(
            user_id=user_id,
            category_id=category.id,
            subcategory_id=subcategory.id if subcategory else None,
            subcategory_name=subcategory.name if subcategory else None,
            amount=float(tx['amount']),
            type=tx.get('type') or 'expense',
            timestamp=_timestamp_from_hint(tx.get('date_hint')),
        )
        db.session.add(transaction)
        db.session.flush()
        saved.append({
            'id': transaction.id,
            'type': transaction.type,
            'amount': transaction.amount,
            'category': category.name,
            'emoji': category.emoji,
        })
    return saved


def _category_suggestions(user_id):
    suggestions = []
    for category in _get_user_categories(user_id):
        suggestions.append({'label': category.name, 'message': category.name})
    return suggestions


def process_chat_message(message, user_id):
    return handle_value_modification(message, user_id)


def handle_value_modification(message, user_id):
    user_session = Session.query.filter_by(user_id=user_id).first() or _get_or_create_user_session(user_id)
    pending = _load_pending(user_session)
    interpreted = interpret_message(message or '')
    kind = interpreted['kind']
    user_categories = _get_user_categories(user_id)

    if message and message.isdigit() and user_session.pending_action and user_session.pending_amount:
        return _legacy_numeric_selection(message, user_id, user_session)

    if kind == 'undo' or (kind == 'reject' and not pending):
        if kind == 'reject' and not pending:
            last_transaction = Transaction.query.filter_by(user_id=user_id).order_by(
                Transaction.timestamp.desc(), Transaction.id.desc()
            ).first()
            if re.search(r'\b(cancelar|cancela|desfazer|anular)\b', fold_chat(message or '')) and last_transaction:
                db.session.delete(last_transaction)
                db.session.commit()
                return _chat_result('Desfiz o último lançamento.', QUICK_SUGGESTIONS, user_id=user_id)
        if kind == 'undo':
            last_transaction = Transaction.query.filter_by(user_id=user_id).order_by(
                Transaction.timestamp.desc(), Transaction.id.desc()
            ).first()
            if not last_transaction:
                return _chat_result('Não há lançamento para desfazer.', QUICK_SUGGESTIONS, user_id=user_id)
            db.session.delete(last_transaction)
            db.session.commit()
            return _chat_result('Desfiz o último lançamento. O saldo já foi atualizado.', QUICK_SUGGESTIONS, user_id=user_id)

    if kind == 'reject' and pending:
        _save_pending(user_session, None)
        db.session.commit()
        return _chat_result('Beleza, não registrei nada.', QUICK_SUGGESTIONS, user_id=user_id)

    if kind == 'confirm' and pending.get('kind') == 'confirm':
        saved = _commit_chat_transactions(user_id, pending.get('transactions') or [])
        _save_pending(user_session, None)
        db.session.commit()
        if not saved:
            return _chat_result('Não encontrei um lançamento para confirmar.', QUICK_SUGGESTIONS, user_id=user_id)
        lines = []
        balances = {item['name']: item['balance'] for item in _category_balances(user_id)}
        for item in saved:
            kind_label = 'receita' if item['type'] == 'income' else 'despesa'
            lines.append(
                f'{item["emoji"]} {kind_label.capitalize()} de {_money(item["amount"])} em {item["category"]}. '
                f'Saldo da categoria: {_money(balances.get(item["category"], 0))}.'
            )
        text = 'Pronto, registrei no seu extrato:\n\n' + '\n'.join(lines) + '\n\nO dashboard já mostra esses valores.'
        return _chat_result(text, QUICK_SUGGESTIONS, saved=saved, user_id=user_id)

    if pending.get('kind') == 'choose_category' and kind not in {'transactions', 'help', 'undo'}:
        chosen = (message or '').strip()
        if chosen.isdigit():
            index = int(chosen) - 1
            if 0 <= index < len(user_categories):
                chosen = user_categories[index].name
            else:
                return _chat_result('Não encontrei essa categoria. Escolhe um dos botões ou escreve o nome.', _category_suggestions(user_id), user_id=user_id)
        if not chosen:
            return _chat_result('Qual o nome da categoria?', _category_suggestions(user_id), user_id=user_id)
        transactions = pending.get('transactions') or []
        index = int(pending.get('index') or 0)
        if 0 <= index < len(transactions):
            transactions[index]['category'] = chosen.strip()
        missing = next((i for i, tx in enumerate(transactions) if not tx.get('category')), None)
        if missing is not None:
            _save_pending(user_session, {'kind': 'choose_category', 'transactions': transactions, 'index': missing})
            db.session.commit()
            amount = transactions[missing].get('amount')
            return _chat_result(
                f'E os {_money(amount)} — em qual categoria?',
                _category_suggestions(user_id),
                user_id=user_id,
            )
        _save_pending(user_session, {'kind': 'confirm', 'transactions': transactions})
        db.session.commit()
        return _chat_result(
            'Entendi assim:\n\n' + _describe_transactions(transactions) + '\n\nPosso registrar?',
            CONFIRM_SUGGESTIONS,
            user_id=user_id,
        )

    if kind == 'help':
        return _chat_result(CHAT_HELP_MESSAGE, QUICK_SUGGESTIONS, user_id=user_id)

    if kind == 'transactions':
        transactions = interpreted['transactions']
        missing = next((i for i, tx in enumerate(transactions) if not tx.get('category')), None)
        if missing is not None:
            _save_pending(user_session, {'kind': 'choose_category', 'transactions': transactions, 'index': missing})
            db.session.commit()
            amount = transactions[missing].get('amount')
            kind_label = 'receita' if transactions[missing].get('type') == 'income' else 'despesa'
            if user_categories:
                names = '\n'.join(f'{i + 1}. {cat.name}' for i, cat in enumerate(user_categories))
                text = f'Anotei uma {kind_label} de {_money(amount)}. Em qual categoria?\n\n{names}\n\nOu escreva um nome novo.'
            else:
                text = f'Anotei uma {kind_label} de {_money(amount)}. Como você quer chamar essa categoria?'
            return _chat_result(text, _category_suggestions(user_id), user_id=user_id)

        _save_pending(user_session, {'kind': 'confirm', 'transactions': transactions})
        db.session.commit()
        return _chat_result(
            'Entendi assim:\n\n' + _describe_transactions(transactions) + '\n\nPosso registrar no seu extrato?',
            CONFIRM_SUGGESTIONS,
            user_id=user_id,
        )

    return _chat_result(
        'Não entendi o valor. Tenta algo como "gastei 45 no mercado" ou "recebi 2500 de salário".',
        QUICK_SUGGESTIONS,
        user_id=user_id,
    )


def _legacy_numeric_selection(message, user_id, user_session):
    if user_session.selected_category:
        category = db.session.get(Category, user_session.selected_category)
        if category:
            subcategories = list(category.subcategories)
            sub_index = int(message) - 1
            if 0 <= sub_index < len(subcategories):
                selected_sub = subcategories[sub_index]
                action = user_session.pending_action
                amount = user_session.pending_amount
                transaction = Transaction(
                    user_id=user_id,
                    category_id=category.id,
                    subcategory_id=selected_sub.id,
                    subcategory_name=selected_sub.name,
                    amount=amount,
                    type='income' if action == 'add' else 'expense',
                    timestamp=datetime.now(),
                )
                db.session.add(transaction)
                _save_pending(user_session, None)
                db.session.commit()
                return _chat_result(
                    f'Registrei {_money(amount)} em {selected_sub.name} ({category.name}).',
                    QUICK_SUGGESTIONS,
                    user_id=user_id,
                )
    cat_index = int(message) - 1
    user_categories = _get_user_categories(user_id)
    if 0 <= cat_index < len(user_categories):
        selected_cat = user_categories[cat_index]
        subcategories = list(selected_cat.subcategories)
        action = user_session.pending_action
        amount = user_session.pending_amount
        if subcategories:
            user_session.selected_category = selected_cat.id
            db.session.commit()
            sub_list = '\n'.join(f'{i + 1}. {sub.name}' for i, sub in enumerate(subcategories))
            return _chat_result(
                f'{selected_cat.name} tem subcategorias. Qual delas?\n\n{sub_list}',
                [{'label': sub.name, 'message': str(i + 1)} for i, sub in enumerate(subcategories)],
                user_id=user_id,
            )
        transaction = Transaction(
            user_id=user_id,
            category_id=selected_cat.id,
            amount=amount,
            type='income' if action == 'add' else 'expense',
            timestamp=datetime.now(),
        )
        db.session.add(transaction)
        _save_pending(user_session, None)
        db.session.commit()
        return _chat_result(
            f'Registrei {_money(amount)} em {selected_cat.name}.',
            QUICK_SUGGESTIONS,
            user_id=user_id,
        )
    return _chat_result('Não encontrei essa opção.', QUICK_SUGGESTIONS, user_id=user_id)

init_database()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5004, debug=False)
