from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate, upgrade
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import bcrypt
import json
import os
import re
from datetime import datetime, timedelta
from config import Config
import requests

app = Flask(__name__)
app.config.from_object(Config)

# Rate limiting
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"  # Can be changed to Redis for production
)

# Simple cache implementation (can be replaced with Redis)
cache = {}

def cache_key(*args, **kwargs):
    """Generate cache key from arguments"""
    key_str = str(args) + str(kwargs)
    return hashlib.md5(key_str.encode()).hexdigest()

def cached(ttl=300):
    """Simple cache decorator"""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            key = cache_key(f.__name__, *args, **kwargs)
            cached_result = cache.get(key)
            
            if cached_result:
                result, timestamp = cached_result
                if datetime.now().timestamp() - timestamp < ttl:
                    return result
            
            result = f(*args, **kwargs)
            cache[key] = (result, datetime.now().timestamp())
            return result
        return wrapped
    return decorator

# Security headers
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

# SQLAlchemy / Migrations setup
db = SQLAlchemy(app)
migrate = Migrate(app, db)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def init_database():
    with app.app_context():
        db.create_all()
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


class Achievement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(200))
    icon = db.Column(db.String(50), default='🏆')
    points = db.Column(db.Integer, default=10)
    achieved_at = db.Column(db.DateTime, default=datetime.now)
    category = db.Column(db.String(50))  # 'savings', 'consistency', 'goals', etc.


class FinancialGoal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    target_amount = db.Column(db.Float, nullable=False)
    current_amount = db.Column(db.Float, default=0.0)
    deadline = db.Column(db.DateTime)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    icon = db.Column(db.String(10), default='🎯')
    color = db.Column(db.String(7), default='#10b981')
    status = db.Column(db.String(20), default='active')  # 'active', 'completed', 'cancelled'
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    def progress_percentage(self):
        if self.target_amount == 0:
            return 0
        return min(100, (self.current_amount / self.target_amount) * 100)


class MonthlyChallenge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    month = db.Column(db.Integer, nullable=False)  # 1-12
    year = db.Column(db.Integer, nullable=False)
    challenge_type = db.Column(db.String(50))  # 'save_percent', 'limit_category', 'no_spending'
    target_value = db.Column(db.Float)
    current_value = db.Column(db.Float, default=0.0)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    completed = db.Column(db.Boolean, default=False)
    points_reward = db.Column(db.Integer, default=50)


class FinancialScore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    score = db.Column(db.Integer, default=0)
    savings_rate = db.Column(db.Float, default=0.0)
    consistency_score = db.Column(db.Integer, default=0)
    goal_completion_rate = db.Column(db.Float, default=0.0)
    updated_at = db.Column(db.DateTime, default=datetime.now)


class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    alert_type = db.Column(db.String(50), nullable=False)  # 'budget', 'recurring', 'low_balance', 'goal'
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), default='info')  # 'info', 'warning', 'critical'
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    action_url = db.Column(db.String(200))  # Optional link to take action


class DashboardConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    layout_config = db.Column(db.Text)  # JSON string for widget layout
    visible_widgets = db.Column(db.Text)  # JSON string for visible widgets
    chart_preferences = db.Column(db.Text)  # JSON string for chart settings
    theme_color = db.Column(db.String(7), default='#2563eb')
    default_chart_type = db.Column(db.String(20), default='pie')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now)


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

    # Enhanced commands
    if re.search(r'\b(desfazer|cancelar|undo|anular)\b', normalized):
        return {'action': 'undo_last_transaction'}
    
    # Help command
    if re.search(r'\b(ajuda|help|como|instruções|instrucoes)\b', normalized):
        return {'action': 'show_help'}
    
    # Balance/saldo command
    if re.search(r'\b(saldo|balance|total|conta)\b', normalized):
        return {'action': 'show_balance'}
    
    # Category analysis
    if re.search(r'\b(analisar|analise|analise|gastos|despesas|categoria)\b', normalized):
        return {'action': 'analyze_spending'}
    
    # Spending by category
    if re.search(r'\b(quanto|quanto gastei|gastos|despesas)\s+(em|com|na|no)\s+(.+)', normalized):
        category_match = re.search(r'\b(quanto|quanto gastei|gastos|despesas)\s+(em|com|na|no)\s+(.+)', normalized)
        if category_match:
            return {'action': 'spending_by_category', 'category': category_match.group(3)}
    
    # Monthly comparison
    if re.search(r'\b(comparar|comparacao|evolução|evolucao)\b', normalized):
        return {'action': 'monthly_comparison'}
    
    # Goals command
    if re.search(r'\b(metas|objetivos|goals)\b', normalized):
        return {'action': 'show_goals'}
    
    # Achievements command
    if re.search(r'\b(conquistas|achievements|badges)\b', normalized):
        return {'action': 'show_achievements'}
    
    # Trend analysis
    if re.search(r'\b(tendencia|tendência|trend|padrão|padrao)\b', normalized):
        return {'action': 'show_trends'}

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
        amount=amount,
        type=tx_type,
        timestamp=timestamp or datetime.now(),
    )
    
    if subcategory:
        transaction.subcategory_id = subcategory.id
        transaction.subcategory_name = subcategory.name
    elif subcategory_name:
        transaction.subcategory_name = subcategory_name

    db.session.add(transaction)
    db.session.commit()
    
    # Run checks after transaction
    check_achievements(user_id, transaction)
    run_alert_checks(user_id)
    
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
@limiter.limit("10 per minute")
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
@limiter.limit("5 per minute")
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
@limiter.limit("30 per minute")
def api_chat():
    data = request.json or {}
    user_message = (data.get('message') or '').strip()

    chat_msg = ChatHistory(user_id=current_user.id, message=user_message, sender='user', timestamp=datetime.now())
    db.session.add(chat_msg)

    response = process_chat_message(user_message, current_user.id)

    bot_msg = ChatHistory(user_id=current_user.id, message=response, sender='bot', timestamp=datetime.now())
    db.session.add(bot_msg)
    db.session.commit()

    return jsonify({'response': response})


@app.route('/api/categories', methods=['GET'])
@login_required
def api_categories():
    categories = _get_user_categories(current_user.id)
    return jsonify([cat.to_dict() for cat in categories])


@app.route('/api/category', methods=['POST'])
@login_required
@limiter.limit("20 per minute")
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


NO_CATEGORIES_MESSAGE = (
    "✨ Bem-vindo(a) ao teu bot de finanças! ✨\n\n"
    "Antes de começares, precisas de ter pelo menos uma categoria criada. É rápido:\n\n"
    "1️⃣ Vai ao Dashboard\n"
    "2️⃣ Na secção \"Categorias\", clica em \"Editar\"\n"
    "3️⃣ Clica em \"Adicionar\", escolhe um nome, emoji e cor\n"
    "4️⃣ Clica em \"Guardar\"\n\n"
    "Depois de teres pelo menos uma categoria, volta aqui e usa o chat para registar valores:\n\n"
    "💰 \"adicionar 50\" — para somar 50€ a uma categoria\n"
    "➖ \"remover 20\" — para subtrair 20€ de uma categoria\n"
)

CHAT_HELP_MESSAGE = (
    "Já tens categorias configuradas! 🙂\n\n"
    "Podes usar frases livres como:\n"
    "- \"gastei 20€ em comida\"\n"
    "- \"recebi 300 de salário\"\n"
    "- \"ontem gastei 15€ em transportes\"\n"
    "- \"desfazer\" / \"cancelar\" para anular a última transação\n\n"
    "Também aceito comandos antigos: \"adicionar 50\" ou \"remover 20\"."
)

ENHANCED_CHAT_HELP_MESSAGE = (
    "🤖 **Comandos Disponíveis:**\n\n"
    "**Gestão de Valores:**\n"
    "• \"adicionar 50\" — somar valor a uma categoria\n"
    "• \"remover 20\" — subtrair valor de uma categoria\n"
    "• \"50 em alimentação\" — adicionar rapidamente a uma categoria específica\n"
    "• \"desfazer\" — cancelar última transação\n\n"
    "**Análise e Informações:**\n"
    "• \"saldo\" ou \"total\" — ver saldo atual\n"
    "• \"gastos\" ou \"despesas\" — análise de gastos\n"
    "• \"quanto gastei em alimentação\" — gastos por categoria\n"
    "• \"comparar\" — comparação mensal\n"
    "• \"tendência\" — análise de tendências\n\n"
    "**Metas e Conquistas:**\n"
    "• \"metas\" — ver as tuas metas financeiras\n"
    "• \"conquistas\" — ver achievements desbloqueados\n\n"
    "**Exemplos de uso:**\n"
    "• \"Adicionar 100€ em salário\"\n"
    "• \"Quanto gastei este mês?\"\n"
    "• \"Mostrar as minhas metas\"\n"
    "• \"Analisar meus gastos\"\n\n"
    "Experimenta os comandos! 🚀"
)


# Alert System
def create_alert(user_id, alert_type, title, message, severity='info', category_id=None, action_url=None):
    """Create a new alert for the user"""
    alert = Alert(
        user_id=user_id,
        alert_type=alert_type,
        title=title,
        message=message,
        severity=severity,
        category_id=category_id,
        action_url=action_url
    )
    db.session.add(alert)
    db.session.commit()
    return alert


def check_budget_alerts(user_id):
    """Check if user is approaching budget limits"""
    budgets = Budget.query.filter_by(user_id=user_id).all()
    
    for budget in budgets:
        # Calculate current spending for this category this month
        from datetime import datetime
        now = datetime.now()
        start_of_month = datetime(now.year, now.month, 1)
        
        current_spending = db.session.query(db.func.sum(Transaction.amount)).filter(
            Transaction.user_id == user_id,
            Transaction.category_id == budget.category_id,
            Transaction.type == 'expense',
            Transaction.timestamp >= start_of_month
        ).scalar() or 0
        
        spending_percentage = (current_spending / budget.monthly_limit) * 100 if budget.monthly_limit > 0 else 0
        
        category = Category.query.get(budget.category_id)
        category_name = category.name if category else "Categoria"
        
        # Check thresholds
        if spending_percentage >= 100:
            create_alert(
                user_id=user_id,
                alert_type='budget',
                title=f'⚠️ Limite Excedido: {category_name}',
                message=f'Excedeste o teu orçamento de €{budget.monthly_limit:.2f} em {category_name}. Gasto atual: €{current_spending:.2f}',
                severity='critical',
                category_id=budget.category_id,
                action_url='/dashboard'
            )
        elif spending_percentage >= 80:
            create_alert(
                user_id=user_id,
                alert_type='budget',
                title=f'📊 Aviso de Orçamento: {category_name}',
                message=f'Já gastaste {spending_percentage:.1f}% do teu orçamento em {category_name}. Gasto atual: €{current_spending:.2f} / €{budget.monthly_limit:.2f}',
                severity='warning',
                category_id=budget.category_id,
                action_url='/dashboard'
            )


def check_recurring_transaction_alerts(user_id):
    """Check for upcoming recurring transactions"""
    from datetime import datetime, timedelta
    
    now = datetime.now()
    upcoming_transactions = RecurringTransaction.query.filter(
        RecurringTransaction.user_id == user_id,
        RecurringTransaction.next_run_date <= now + timedelta(days=3),
        RecurringTransaction.next_run_date >= now
    ).all()
    
    for recurring in upcoming_transactions:
        category = Category.query.get(recurring.category_id)
        category_name = category.name if category else "Categoria"
        
        create_alert(
            user_id=user_id,
            alert_type='recurring',
            title=f'🔄 Transação Recorrente: {category_name}',
            message=f'Tens uma transação recorrente de €{recurring.amount:.2f} em {category_name} prevista para {recurring.next_run_date.strftime("%d/%m/%Y")}',
            severity='info',
            category_id=recurring.category_id,
            action_url='/dashboard'
        )


def check_low_balance_alert(user_id):
    """Check if user has low balance"""
    total_income = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.type == 'income'
    ).scalar() or 0
    
    total_expenses = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.type == 'expense'
    ).scalar() or 0
    
    balance = total_income - total_expenses
    
    # Alert if balance is low (less than 100€ or negative)
    if balance < 0:
        create_alert(
            user_id=user_id,
            alert_type='low_balance',
            title='🚨 Saldo Negativo!',
            message=f'O teu saldo atual é negativo: €{balance:.2f}. Considera rever os teus gastos.',
            severity='critical',
            action_url='/dashboard'
        )
    elif balance < 100:
        create_alert(
            user_id=user_id,
            alert_type='low_balance',
            title='💰 Saldo Baixo',
            message=f'O teu saldo atual é baixo: €{balance:.2f}. Mantém atenção aos teus gastos.',
            severity='warning',
            action_url='/dashboard'
        )


def check_goal_progress_alerts(user_id):
    """Check goal progress and send alerts"""
    goals = FinancialGoal.query.filter_by(user_id=user_id, status='active').all()
    
    for goal in goals:
        progress = goal.progress_percentage()
        
        # Alert if goal is nearly complete (90%+)
        if progress >= 90 and progress < 100:
            create_alert(
                user_id=user_id,
                alert_type='goal',
                title=f'🎯 Quase Lá! {goal.name}',
                message=f'A tua meta "{goal.name}" está a {progress:.1f}%! Continua assim!',
                severity='info',
                action_url='/dashboard'
            )
        
        # Alert if goal deadline is approaching and progress is low
        if goal.deadline:
            days_remaining = (goal.deadline - datetime.now()).days
            if days_remaining <= 7 and progress < 50:
                create_alert(
                    user_id=user_id,
                    alert_type='goal',
                    title=f'⏰ Prazo Aproximando: {goal.name}',
                    message=f'O prazo para a tua meta "{goal.name}" termina em {days_remaining} dias e estás apenas a {progress:.1f}%.',
                    severity='warning',
                    action_url='/dashboard'
                )


def run_alert_checks(user_id):
    """Run all alert checks for a user"""
    try:
        check_budget_alerts(user_id)
        check_recurring_transaction_alerts(user_id)
        check_low_balance_alert(user_id)
        check_goal_progress_alerts(user_id)
    except Exception as e:
        print(f"Error running alert checks: {e}")


# API Routes for Alerts
@app.route('/api/alerts')
@login_required
def get_alerts():
    alerts = Alert.query.filter_by(user_id=current_user.id).order_by(Alert.created_at.desc()).limit(20).all()
    return jsonify([{
        'id': a.id,
        'type': a.alert_type,
        'title': a.title,
        'message': a.message,
        'severity': a.severity,
        'is_read': a.is_read,
        'created_at': a.created_at.isoformat(),
        'action_url': a.action_url
    } for a in alerts])


@app.route('/api/alerts/<int:alert_id>/read', methods=['POST'])
@login_required
def mark_alert_read(alert_id):
    alert = Alert.query.get_or_404(alert_id)
    if alert.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    alert.is_read = True
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/alerts/check', methods=['POST'])
@login_required
def check_user_alerts():
    """Manually trigger alert checks"""
    run_alert_checks(current_user.id)
    return jsonify({'success': True, 'message': 'Alert checks completed'})


# Enhanced chat functions
def get_balance_summary(user_id):
    """Get current balance summary"""
    total_income = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.type == 'income'
    ).scalar() or 0
    
    total_expenses = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.type == 'expense'
    ).scalar() or 0
    
    balance = total_income - total_expenses
    
    return (
        f"💰 **Resumo Financeiro:**\n\n"
        f"• Total Receitas: €{total_income:.2f}\n"
        f"• Total Despesas: €{total_expenses:.2f}\n"
        f"• Saldo Atual: €{balance:.2f}\n"
        f"• Taxa de Poupança: {(balance/total_income*100 if total_income > 0 else 0):.1f}%"
    )


def analyze_spending(user_id):
    """Analyze spending patterns"""
    transactions = Transaction.query.filter_by(user_id=user_id, type='expense').all()
    
    if not transactions:
        return "Ainda não tens despesas registadas."
    
    # Group by category
    category_spending = {}
    for tx in transactions:
        category = Category.query.get(tx.category_id)
        if category:
            category_name = category.name
            category_spending[category_name] = category_spending.get(category_name, 0) + tx.amount
    
    total_spent = sum(category_spending.values())
    
    # Find highest spending category
    highest_category = max(category_spending.items(), key=lambda x: x[1]) if category_spending else None
    
    response = f"📊 **Análise de Gastos:**\n\n"
    response += f"• Total Gasto: €{total_spent:.2f}\n"
    
    if highest_category:
        response += f"• Categoria com maior gasto: {highest_category[0]} (€{highest_category[1]:.2f})\n"
    
    response += "\n**Gastos por Categoria:**\n"
    for category, amount in sorted(category_spending.items(), key=lambda x: x[1], reverse=True):
        percentage = (amount / total_spent * 100) if total_spent > 0 else 0
        response += f"• {category}: €{amount:.2f} ({percentage:.1f}%)\n"
    
    return response


def get_spending_by_category(user_id, category_name):
    """Get spending for a specific category"""
    if not category_name:
        return "Por favor especifica a categoria. Ex: 'quanto gastei em alimentação'"
    
    category = _resolve_category(user_id, category_name)
    if not category:
        return f"Categoria '{category_name}' não encontrada."
    
    total_spent = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.category_id == category.id,
        Transaction.type == 'expense'
    ).scalar() or 0
    
    # Get spending by subcategory
    subcategory_spending = {}
    transactions = Transaction.query.filter_by(
        user_id=user_id,
        category_id=category.id,
        type='expense'
    ).all()
    
    for tx in transactions:
        subcat_name = tx.subcategory_name or "Geral"
        subcategory_spending[subcat_name] = subcategory_spending.get(subcat_name, 0) + tx.amount
    
    response = f"📈 **Gastos em {category.name}:**\n\n"
    response += f"• Total: €{total_spent:.2f}\n"
    
    if subcategory_spending:
        response += "\n**Por Subcategoria:**\n"
        for subcat, amount in sorted(subcategory_spending.items(), key=lambda x: x[1], reverse=True):
            response += f"• {subcat}: €{amount:.2f}\n"
    
    return response


def get_monthly_comparison(user_id):
    """Compare spending between months"""
    from collections import defaultdict
    
    transactions = Transaction.query.filter_by(user_id=user_id, type='expense').all()
    
    if not transactions:
        return "Ainda não tens dados suficientes para comparação mensal."
    
    monthly_spending = defaultdict(float)
    for tx in transactions:
        month_key = f"{tx.timestamp.year}-{tx.timestamp.month:02d}"
        monthly_spending[month_key] += tx.amount
    
    if len(monthly_spending) < 2:
        return "Precisas de pelo menos 2 meses de dados para comparação."
    
    response = "📅 **Comparação Mensal:**\n\n"
    
    # Sort by month
    sorted_months = sorted(monthly_spending.keys())
    for month in sorted_months:
        response += f"• {month}: €{monthly_spending[month]:.2f}\n"
    
    # Calculate trend
    if len(sorted_months) >= 2:
        latest = monthly_spending[sorted_months[-1]]
        previous = monthly_spending[sorted_months[-2]]
        difference = latest - previous
        percentage = (difference / previous * 100) if previous > 0 else 0
        
        trend = "aumentou" if difference > 0 else "diminuiu"
        response += f"\n📊 Em relação ao mês anterior, os gastos {trend} {abs(percentage):.1f}%"
    
    return response


def get_goals_summary(user_id):
    """Get summary of financial goals"""
    goals = FinancialGoal.query.filter_by(user_id=user_id, status='active').all()
    
    if not goals:
        return "🎯 **Metas:**\n\nNão tens metas ativas. Define metas no Dashboard para começar!"
    
    response = "🎯 **Metas Financeiras:**\n\n"
    for goal in goals:
        progress = goal.progress_percentage()
        response += f"• {goal.icon} {goal.name}: {progress:.1f}% (€{goal.current_amount:.2f} / €{goal.target_amount:.2f})\n"
    
    return response


def get_achievements_summary(user_id):
    """Get summary of achievements"""
    achievements = Achievement.query.filter_by(user_id=user_id).order_by(Achievement.achieved_at.desc()).limit(10).all()
    
    if not achievements:
        return "🏆 **Conquistas:**\n\nAinda não desbloqueaste conquistas. Continua a registar transações!"
    
    total_points = sum(a.points for a in Achievement.query.filter_by(user_id=user_id).all())
    
    response = f"🏆 **Conquistas (Total: {total_points} pontos):**\n\n"
    for achievement in achievements:
        response += f"• {achievement.icon} {achievement.name} (+{achievement.points} pts)\n"
    
    return response


def get_spending_trends(user_id):
    """Analyze spending trends"""
    from collections import defaultdict
    
    transactions = Transaction.query.filter_by(user_id=user_id, type='expense').all()
    
    if not transactions:
        return "Ainda não tens dados suficientes para análise de tendências."
    
    # Analyze by day of week
    day_spending = defaultdict(float)
    for tx in transactions:
        day_name = tx.timestamp.strftime('%A')
        day_spending[day_name] += tx.amount
    
    # Find most expensive day
    most_expensive_day = max(day_spending.items(), key=lambda x: x[1]) if day_spending else None
    
    response = "📈 **Análise de Tendências:**\n\n"
    
    if most_expensive_day:
        response += f"• Dia com mais gastos: {most_expensive_day[0]} (€{most_expensive_day[1]:.2f})\n"
    
    # Analyze category trends
    category_trends = defaultdict(list)
    for tx in transactions:
        category = Category.query.get(tx.category_id)
        if category:
            month_key = f"{tx.timestamp.year}-{tx.timestamp.month:02d}"
            category_trends[category.name].append((month_key, tx.amount))
    
    response += "\n**Tendências por Categoria:**\n"
    for category, tx_list in category_trends.items():
        if len(tx_list) >= 2:
            recent_avg = sum(amount for _, amount in tx_list[-3:]) / min(3, len(tx_list))
            response += f"• {category}: média recente €{recent_avg:.2f}\n"
    
    return response


def handle_undo_transaction(user_id):
    """Handle undo transaction command"""
    last_transaction = Transaction.query.filter_by(user_id=user_id).order_by(
        Transaction.timestamp.desc(), Transaction.id.desc()
    ).first()
    
    if not last_transaction:
        return 'Não há transações para desfazer.'
    
    category = Category.query.get(last_transaction.category_id)
    category_name = category.name if category else "categoria apagada"
    
    db.session.delete(last_transaction)
    db.session.commit()
    
    return f'✅ Desfeita a última transação em {category_name} (€{last_transaction.amount:.2f}).'


def handle_quick_add(user_id, amount, category_name, user_categories):
    """Handle quick add to specific category"""
    if not amount or not category_name:
        return "Formato incorreto. Ex: '50 em alimentação'"
    
    category = _resolve_category(user_id, category_name, user_categories)
    if not category:
        return f"Categoria '{category_name}' não encontrada. Tenta 'adicionar {amount}' para escolher a categoria."
    
    # Check if category has subcategories
    if category.subcategories:
        sub_list = "\n".join([f"{i+1}. {sub.name}" for i, sub in enumerate(category.subcategories)])
        user_session = _get_or_create_user_session(user_id)
        user_session.pending_action = 'add'
        user_session.pending_amount = amount
        user_session.selected_category = category.id
        db.session.commit()
        
        return f"A categoria {category.name} tem subcategorias:\n{sub_list}\n\nQual delas queres alterar?"
    
    # Direct add to category
    transaction = Transaction(
        user_id=user_id,
        category_id=category.id,
        amount=amount,
        type='income',
        timestamp=datetime.now(),
    )
    db.session.add(transaction)
    db.session.commit()
    
    # Check for achievements
    check_achievements(user_id, transaction)
    
    return f"✅ Adicionados €{amount:.2f} a {category.name}!"


def process_chat_message(message, user_id):
    user_categories = _get_user_categories(user_id)
    if not user_categories:
        return NO_CATEGORIES_MESSAGE
    
    # Process enhanced commands
    intent = parse_transaction_intent(message, user_id, user_categories)
    
    if intent['action'] == 'show_help':
        return ENHANCED_CHAT_HELP_MESSAGE
    
    if intent['action'] == 'show_balance':
        return get_balance_summary(user_id)
    
    if intent['action'] == 'analyze_spending':
        return analyze_spending(user_id)
    
    if intent['action'] == 'spending_by_category':
        return get_spending_by_category(user_id, intent.get('category'))
    
    if intent['action'] == 'monthly_comparison':
        return get_monthly_comparison(user_id)
    
    if intent['action'] == 'show_goals':
        return get_goals_summary(user_id)
    
    if intent['action'] == 'show_achievements':
        return get_achievements_summary(user_id)
    
    if intent['action'] == 'show_trends':
        return get_spending_trends(user_id)
    
    if intent['action'] == 'undo_last_transaction':
        return handle_undo_transaction(user_id)
    
    # Handle quick add to specific category
    if intent['action'] == 'add' and intent.get('category'):
        return handle_quick_add(user_id, intent['amount'], intent['category'], user_categories)
    
    return handle_value_modification(message, user_id)


def handle_value_modification(message, user_id):
    user_session = Session.query.filter_by(user_id=user_id).first() or _get_or_create_user_session(user_id)

    if not message:
        return CHAT_HELP_MESSAGE

    normalized = normalize_text(message)
    if normalized in {'oi', 'olá', 'ola', 'hello', 'hi', 'ajuda', 'help', 'como funciona'}:
        return CHAT_HELP_MESSAGE

    # Check for explicit "adicionar" command
    if normalized.startswith('adicionar'):
        try:
            amount = float(normalized.split()[1])
            user_session.pending_action = 'add'
            user_session.pending_amount = amount
            user_session.pending_intent = None
            db.session.commit()
            user_categories = _get_user_categories(user_id)
            cat_list = "\n".join([f"{i+1}. {cat.name}" for i, cat in enumerate(user_categories)])
            return f"Em que categoria queres adicionar {amount:.2f}€?\n\n{cat_list}"
        except (IndexError, ValueError):
            return 'Formato incorreto. Exemplo: adicionar 400'

    # Check for explicit "remover" command
    if normalized.startswith('remover'):
        try:
            amount = float(normalized.split()[1])
            user_session.pending_action = 'remove'
            user_session.pending_amount = amount
            user_session.pending_intent = None
            db.session.commit()
            user_categories = _get_user_categories(user_id)
            cat_list = "\n".join([f"{i+1}. {cat.name}" for i, cat in enumerate(user_categories)])
            return f"Em que categoria queres remover {amount:.2f}€?\n\n{cat_list}"
        except (IndexError, ValueError):
            return 'Formato incorreto. Exemplo: remover 400'

    # Undo/cancel last transaction (DO NOT ALTER)
    if re.search(r'\b(desfazer|cancelar|undo|anular)\b', normalized):
        last_transaction = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.timestamp.desc(), Transaction.id.desc()).first()
        if not last_transaction:
            return 'Não há transações para desfazer.'
        db.session.delete(last_transaction)
        db.session.commit()
        return '✅ Desfeita a última transação.'

    if message.isdigit() and user_session and user_session.selected_category:
        category = Category.query.get(user_session.selected_category)
        if category:
            subcategories = list(category.subcategories)
            if subcategories:
                sub_index = int(message) - 1
                if 0 <= sub_index < len(subcategories):
                    selected_sub = subcategories[sub_index]
                    action = user_session.pending_action
                    amount = user_session.pending_amount
                    transaction_type = 'income' if action == 'add' else 'expense'
                    transaction = Transaction(
                        user_id=user_id,
                        category_id=category.id,
                        subcategory_id=selected_sub.id,
                        subcategory_name=selected_sub.name,
                        amount=amount,
                        type=transaction_type,
                        timestamp=datetime.now(),
                    )
                    db.session.add(transaction)
                    user_session.pending_action = None
                    user_session.pending_amount = None
                    user_session.selected_category = None
                    db.session.commit()
                    return f"✅ {amount:.2f}€ {'adicionado a' if action == 'add' else 'removido de'} {selected_sub.name} ({category.name})!"

    if message.isdigit() and user_session and user_session.pending_action and not user_session.selected_category:
        cat_index = int(message) - 1
        user_categories = _get_user_categories(user_id)
        if 0 <= cat_index < len(user_categories):
            selected_cat = user_categories[cat_index]
            action = user_session.pending_action
            amount = user_session.pending_amount
            subcategories = list(selected_cat.subcategories)
            if subcategories:
                sub_list = "\n".join([f"{i+1}. {sub.name}" for i, sub in enumerate(subcategories)])
                user_session.selected_category = selected_cat.id
                db.session.commit()
                return f"A categoria {selected_cat.name} tem subcategorias: {', '.join(sub.name for sub in subcategories)}.\nQual delas queres alterar?\n\n{sub_list}"

            transaction_type = 'income' if action == 'add' else 'expense'
            transaction = Transaction(
                user_id=user_id,
                category_id=selected_cat.id,
                amount=amount,
                type=transaction_type,
                timestamp=datetime.now(),
            )
            db.session.add(transaction)
            user_session.pending_action = None
            user_session.pending_amount = None
            user_session.pending_intent = None
            db.session.commit()
            return f"✅ {amount:.2f}€ {'adicionado a' if action == 'add' else 'removido de'} {selected_cat.name}!"

    return 'Comando não reconhecido. Escreve "adicionar" ou "remover" seguido do valor em €.'


# Gamification System
def check_achievements(user_id, transaction=None):
    """Check and award achievements based on user actions"""
    achievements_to_award = []
    
    # First transaction achievement
    user_transactions = Transaction.query.filter_by(user_id=user_id).count()
    if user_transactions == 1:
        achievements_to_award.append({
            'name': 'Primeira Transação',
            'description': 'Registaste a tua primeira transação!',
            'icon': '🎉',
            'points': 10,
            'category': 'consistency'
        })
    
    # Transaction milestones
    if user_transactions == 10:
        achievements_to_award.append({
            'name': 'Dedicação',
            'description': 'Registaste 10 transações!',
            'icon': '⭐',
            'points': 25,
            'category': 'consistency'
        })
    elif user_transactions == 50:
        achievements_to_award.append({
            'name': 'Compromisso',
            'description': 'Registaste 50 transações!',
            'icon': '🌟',
            'points': 50,
            'category': 'consistency'
        })
    
    # Check if achievement already exists and award new ones
    for achievement_data in achievements_to_award:
        existing = Achievement.query.filter_by(
            user_id=user_id,
            name=achievement_data['name']
        ).first()
        
        if not existing:
            achievement = Achievement(
                user_id=user_id,
                **achievement_data
            )
            db.session.add(achievement)
            
            # Update financial score
            financial_score = FinancialScore.query.filter_by(user_id=user_id).first()
            if not financial_score:
                financial_score = FinancialScore(user_id=user_id)
                db.session.add(financial_score)
            
            financial_score.score += achievement_data['points']
            financial_score.updated_at = datetime.now()
    
    # Check goal completion
    goals = FinancialGoal.query.filter_by(user_id=user_id, status='active').all()
    for goal in goals:
        if goal.progress_percentage() >= 100:
            goal.status = 'completed'
            achievement = Achievement(
                user_id=user_id,
                name=f'Meta Atingida: {goal.name}',
                description=f'Alcançaste a tua meta de {goal.target_amount:.2f}€!',
                icon='🎯',
                points=100,
                category='goals'
            )
            db.session.add(achievement)
            
            financial_score = FinancialScore.query.filter_by(user_id=user_id).first()
            if not financial_score:
                financial_score = FinancialScore(user_id=user_id)
                db.session.add(financial_score)
            
            financial_score.score += 100
            financial_score.goal_completion_rate = min(100, financial_score.goal_completion_rate + 10)
            financial_score.updated_at = datetime.now()
    
    db.session.commit()
    return achievements_to_award


def calculate_financial_score(user_id):
    """Calculate comprehensive financial health score"""
    financial_score = FinancialScore.query.filter_by(user_id=user_id).first()
    if not financial_score:
        financial_score = FinancialScore(user_id=user_id)
        db.session.add(financial_score)
    
    # Calculate savings rate
    total_income = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.type == 'income'
    ).scalar() or 0
    
    total_expenses = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.type == 'expense'
    ).scalar() or 0
    
    if total_income > 0:
        savings_rate = ((total_income - total_expenses) / total_income) * 100
        financial_score.savings_rate = max(0, savings_rate)
    
    # Calculate consistency score (based on regular transactions)
    from collections import defaultdict
    transactions_by_month = defaultdict(int)
    transactions = Transaction.query.filter_by(user_id=user_id).all()
    
    for tx in transactions:
        month_key = f"{tx.timestamp.year}-{tx.timestamp.month}"
        transactions_by_month[month_key] += 1
    
    if transactions_by_month:
        avg_transactions = sum(transactions_by_month.values()) / len(transactions_by_month)
        financial_score.consistency_score = min(100, avg_transactions * 10)
    
    financial_score.updated_at = datetime.now()
    db.session.commit()
    
    return financial_score


# API Routes for Gamification
@app.route('/api/achievements')
@login_required
def get_achievements():
    achievements = Achievement.query.filter_by(user_id=current_user.id).order_by(Achievement.achieved_at.desc()).all()
    return jsonify([{
        'id': a.id,
        'name': a.name,
        'description': a.description,
        'icon': a.icon,
        'points': a.points,
        'achieved_at': a.achieved_at.isoformat(),
        'category': a.category
    } for a in achievements])


@app.route('/api/financial-score')
@login_required
def get_financial_score():
    calculate_financial_score(current_user.id)
    score = FinancialScore.query.filter_by(user_id=current_user.id).first()
    
    if not score:
        score = FinancialScore(user_id=current_user.id)
        db.session.add(score)
        db.session.commit()
    
    return jsonify({
        'score': score.score,
        'savings_rate': score.savings_rate,
        'consistency_score': score.consistency_score,
        'goal_completion_rate': score.goal_completion_rate,
        'updated_at': score.updated_at.isoformat()
    })


@app.route('/api/goals', methods=['GET', 'POST'])
@login_required
def manage_goals():
    if request.method == 'POST':
        data = request.json
        goal = FinancialGoal(
            user_id=current_user.id,
            name=data['name'],
            target_amount=data['target_amount'],
            current_amount=data.get('current_amount', 0),
            deadline=datetime.fromisoformat(data['deadline']) if data.get('deadline') else None,
            category_id=data.get('category_id'),
            icon=data.get('icon', '🎯'),
            color=data.get('color', '#10b981')
        )
        db.session.add(goal)
        db.session.commit()
        return jsonify({'success': True, 'goal_id': goal.id})
    
    goals = FinancialGoal.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        'id': g.id,
        'name': g.name,
        'target_amount': g.target_amount,
        'current_amount': g.current_amount,
        'progress': g.progress_percentage(),
        'deadline': g.deadline.isoformat() if g.deadline else None,
        'icon': g.icon,
        'color': g.color,
        'status': g.status
    } for g in goals])


@app.route('/api/goals/<int:goal_id>', methods=['PUT', 'DELETE'])
@login_required
def update_goal(goal_id):
    goal = FinancialGoal.query.get_or_404(goal_id)
    if goal.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    if request.method == 'DELETE':
        db.session.delete(goal)
        db.session.commit()
        return jsonify({'success': True})
    
    data = request.json
    if 'current_amount' in data:
        goal.current_amount = data['current_amount']
    if 'name' in data:
        goal.name = data['name']
    if 'target_amount' in data:
        goal.target_amount = data['target_amount']
    if 'status' in data:
        goal.status = data['status']
    
    db.session.commit()
    check_achievements(current_user.id)
    return jsonify({'success': True})


@app.route('/api/challenges', methods=['GET', 'POST'])
@login_required
def manage_challenges():
    if request.method == 'POST':
        data = request.json
        challenge = MonthlyChallenge(
            user_id=current_user.id,
            month=data['month'],
            year=data['year'],
            challenge_type=data['challenge_type'],
            target_value=data['target_value'],
            category_id=data.get('category_id'),
            points_reward=data.get('points_reward', 50)
        )
        db.session.add(challenge)
        db.session.commit()
        return jsonify({'success': True, 'challenge_id': challenge.id})
    
    challenges = MonthlyChallenge.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        'id': c.id,
        'month': c.month,
        'year': c.year,
        'challenge_type': c.challenge_type,
        'target_value': c.target_value,
        'current_value': c.current_value,
        'completed': c.completed,
        'points_reward': c.points_reward
    } for c in challenges])


# Enhanced Dashboard with Gamification
@app.route('/dashboard')
@login_required
def dashboard():
    user_categories = _get_user_categories(current_user.id)
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    
    # Calculate total spent
    total_spent = sum(tx.amount for tx in transactions if tx.type == 'expense')
    total_income = sum(tx.amount for tx in transactions if tx.type == 'income')
    
    # Get financial score
    financial_score = calculate_financial_score(current_user.id)
    
    # Get recent achievements
    recent_achievements = Achievement.query.filter_by(user_id=current_user.id).order_by(
        Achievement.achieved_at.desc()
    ).limit(5).all()
    
    # Get active goals
    active_goals = FinancialGoal.query.filter_by(
        user_id=current_user.id,
        status='active'
    ).all()
    
    # Additional data for charts
    category_totals = {}
    highest_spending = None
    max_amount = 0
    
    for cat in user_categories:
        cat_expense = sum(t.amount for t in transactions if t.category_id == cat.id and t.type == 'expense')
        cat_income = sum(t.amount for t in transactions if t.category_id == cat.id and t.type == 'income')
        category_totals[cat.id] = {'expense': cat_expense, 'income': cat_income, 'net': cat_income - cat_expense}
        
        if cat_expense > max_amount:
            max_amount = cat_expense
            highest_spending = cat
    
    budgets = {
        category.id: Budget.query.filter_by(user_id=current_user.id, category_id=category.id).first()
        for category in user_categories
    }
    
    return render_template('dashboard.html',
                         categories=user_categories,
                         transactions=transactions,
                         total_spent=total_spent,
                         total_income=total_income,
                         financial_score=financial_score,
                         recent_achievements=recent_achievements,
                         active_goals=active_goals,
                         category_totals=category_totals,
                         highest_spending=highest_spending,
                         budgets=budgets,
                         categories_json=[{
                             'id': cat.id,
                             'name': cat.name,
                             'emoji': cat.emoji,
                             'color': cat.color,
                             'subcategories': [sub.name for sub in cat.subcategories]
                         } for cat in user_categories])


# API Documentation
@app.route('/api/docs')
@login_required
def api_docs():
    """API Documentation endpoint"""
    return jsonify({
        'title': 'FinancialBot API',
        'version': '2.0',
        'endpoints': {
            'authentication': {
                'POST /register': 'Create new user account',
                'POST /login': 'Authenticate user',
                'POST /logout': 'Logout user',
                'POST /recover': 'Recover password'
            },
            'categories': {
                'GET /api/categories': 'Get all user categories',
                'POST /api/category': 'Create new category',
                'GET /api/category/<id>': 'Get category details',
                'PUT /api/category/<id>': 'Update category',
                'DELETE /api/category/<id>': 'Delete category',
                'POST /api/category/<id>/move': 'Move transactions to another category'
            },
            'transactions': {
                'GET /api/transactions': 'Get all transactions',
                'POST /api/transaction': 'Create transaction',
                'DELETE /api/transaction/<id>': 'Delete transaction'
            },
            'chat': {
                'POST /api/chat': 'Send message to chat bot',
                'GET /api/chat-history': 'Get chat history'
            },
            'gamification': {
                'GET /api/achievements': 'Get user achievements',
                'GET /api/financial-score': 'Get financial health score',
                'GET /api/goals': 'Get financial goals',
                'POST /api/goals': 'Create new goal',
                'PUT /api/goals/<id>': 'Update goal',
                'DELETE /api/goals/<id>': 'Delete goal',
                'GET /api/challenges': 'Get monthly challenges',
                'POST /api/challenges': 'Create new challenge'
            },
            'alerts': {
                'GET /api/alerts': 'Get user alerts',
                'POST /api/alerts/<id>/read': 'Mark alert as read',
                'POST /api/alerts/check': 'Trigger alert checks'
            },
            'analytics': {
                'GET /api/analytics/spending': 'Get spending analytics',
                'GET /api/analytics/trends': 'Get spending trends',
                'GET /api/analytics/predictions': 'Get ML predictions'
            }
        },
        'authentication': 'All endpoints require JWT token or session cookie',
        'format': 'JSON',
        'base_url': 'http://localhost:5001/api'
    })


# External API Integrations
class ExternalIntegration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    integration_type = db.Column(db.String(50), nullable=False)  # 'bank', 'currency', 'investment'
    provider_name = db.Column(db.String(100))
    api_key = db.Column(db.String(200))  # Encrypted in production
    config = db.Column(db.Text)  # JSON string for additional config
    is_active = db.Column(db.Boolean, default=True)
    last_sync = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)


# Currency Exchange Integration
def get_exchange_rates(base_currency='EUR'):
    """Get current exchange rates from external API"""
    try:
        # Using free exchange rate API
        response = requests.get(
            f'https://api.exchangerate-api.com/v4/latest/{base_currency}',
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            return {
                'success': True,
                'base': data.get('base'),
                'rates': data.get('rates', {}),
                'date': data.get('date')
            }
        else:
            return {'success': False, 'error': 'API request failed'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def convert_currency(amount, from_currency, to_currency):
    """Convert amount between currencies"""
    if from_currency == to_currency:
        return amount
    
    try:
        rates_data = get_exchange_rates(from_currency)
        
        if rates_data['success']:
            rates = rates_data['rates']
            if to_currency in rates:
                return amount * rates[to_currency]
        
        return None
    except Exception:
        return None


# Integration API Routes
@app.route('/api/integrations/currency/rates')
@login_required
def currency_rates():
    """Get current currency exchange rates"""
    base_currency = request.args.get('base', 'EUR')
    rates = get_exchange_rates(base_currency)
    return jsonify(rates)


@app.route('/api/integrations/currency/convert')
@login_required
def currency_convert():
    """Convert currency amount"""
    amount = float(request.args.get('amount', 0))
    from_currency = request.args.get('from', 'EUR')
    to_currency = request.args.get('to', 'USD')
    
    converted = convert_currency(amount, from_currency, to_currency)
    
    if converted is not None:
        return jsonify({
            'success': True,
            'original': {'amount': amount, 'currency': from_currency},
            'converted': {'amount': converted, 'currency': to_currency},
            'rate': converted / amount if amount > 0 else 0
        })
    else:
        return jsonify({'success': False, 'error': 'Conversion failed'})


@app.route('/api/integrations', methods=['GET', 'POST'])
@login_required
def manage_integrations():
    """Manage external integrations"""
    if request.method == 'POST':
        data = request.json
        
        integration = ExternalIntegration(
            user_id=current_user.id,
            integration_type=data['integration_type'],
            provider_name=data.get('provider_name'),
            api_key=data.get('api_key'),
            config=json.dumps(data.get('config', {}))
        )
        
        db.session.add(integration)
        db.session.commit()
        
        return jsonify({'success': True, 'integration_id': integration.id})
    
    # GET request
    integrations = ExternalIntegration.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        'id': i.id,
        'integration_type': i.integration_type,
        'provider_name': i.provider_name,
        'is_active': i.is_active,
        'last_sync': i.last_sync.isoformat() if i.last_sync else None
    } for i in integrations])


@app.route('/api/integrations/<int:integration_id>', methods=['DELETE'])
@login_required
def delete_integration(integration_id):
    """Delete an integration"""
    integration = ExternalIntegration.query.get_or_404(integration_id)
    
    if integration.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db.session.delete(integration)
    db.session.commit()
    
    return jsonify({'success': True})


# Reports and Insights
@app.route('/reports')
@login_required
def reports():
    """Reports page"""
    return render_template('reports.html')


@app.route('/api/reports/monthly')
@login_required
def monthly_report():
    """Generate monthly financial report"""
    from datetime import datetime, timedelta
    from collections import defaultdict
    
    now = datetime.now()
    start_of_month = datetime(now.year, now.month, 1)
    end_of_month = datetime(now.year, now.month + 1, 1) - timedelta(days=1)
    
    # Get this month's transactions
    transactions = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        Transaction.timestamp >= start_of_month,
        Transaction.timestamp <= end_of_month
    ).all()
    
    # Calculate totals
    total_income = sum(tx.amount for tx in transactions if tx.type == 'income')
    total_expenses = sum(tx.amount for tx in transactions if tx.type == 'expense')
    net_balance = total_income - total_expenses
    
    # Category breakdown
    category_breakdown = defaultdict(lambda: {'income': 0, 'expense': 0})
    for tx in transactions:
        category = Category.query.get(tx.category_id)
        if category:
            category_breakdown[category.name][tx.type] += tx.amount
    
    # Daily spending pattern
    daily_spending = defaultdict(float)
    for tx in transactions:
        if tx.type == 'expense':
            day_key = tx.timestamp.strftime('%Y-%m-%d')
            daily_spending[day_key] += tx.amount
    
    # Insights
    insights = []
    if total_expenses > total_income:
        insights.append({
            'type': 'warning',
            'message': f'Gastaste €{total_expenses - total_income:.2f} mais do que ganhaste este mês.'
        })
    
    if category_breakdown:
        highest_expense = max(category_breakdown.items(), key=lambda x: x[1]['expense'])
        insights.append({
            'type': 'info',
            'message': f'A tua maior despesa foi em {highest_expense[0]}: €{highest_expense[1]["expense"]:.2f}'
        })
    
    savings_rate = (net_balance / total_income * 100) if total_income > 0 else 0
    if savings_rate > 20:
        insights.append({
            'type': 'success',
            'message': f'Excelente! Poupanste {savings_rate:.1f}% dos teus rendimentos.'
        })
    
    return jsonify({
        'period': {
            'start': start_of_month.isoformat(),
            'end': end_of_month.isoformat(),
            'month': now.strftime('%B %Y')
        },
        'summary': {
            'total_income': total_income,
            'total_expenses': total_expenses,
            'net_balance': net_balance,
            'savings_rate': savings_rate
        },
        'category_breakdown': dict(category_breakdown),
        'daily_spending': dict(daily_spending),
        'insights': insights,
        'transaction_count': len(transactions)
    })


@app.route('/api/reports/annual')
@login_required
def annual_report():
    """Generate annual financial report"""
    from collections import defaultdict
    
    now = datetime.now()
    start_of_year = datetime(now.year, 1, 1)
    
    transactions = Transaction.query.filter(
        Transaction.user_id == current_user.id,
        Transaction.timestamp >= start_of_year
    ).all()
    
    # Monthly breakdown
    monthly_data = defaultdict(lambda: {'income': 0, 'expense': 0})
    for tx in transactions:
        month_key = f"{tx.timestamp.year}-{tx.timestamp.month:02d}"
        monthly_data[month_key][tx.type] += tx.amount
    
    # Category totals for the year
    category_totals = defaultdict(float)
    for tx in transactions:
        if tx.type == 'expense':
            category = Category.query.get(tx.category_id)
            if category:
                category_totals[category.name] += tx.amount
    
    # Yearly totals
    total_income = sum(tx.amount for tx in transactions if tx.type == 'income')
    total_expenses = sum(tx.amount for tx in transactions if tx.type == 'expense')
    
    return jsonify({
        'year': now.year,
        'summary': {
            'total_income': total_income,
            'total_expenses': total_expenses,
            'net_balance': total_income - total_expenses,
            'savings_rate': ((total_income - total_expenses) / total_income * 100) if total_income > 0 else 0
        },
        'monthly_breakdown': dict(monthly_data),
        'category_totals': dict(category_totals),
        'transaction_count': len(transactions)
    })


@app.route('/api/reports/export')
@login_required
def export_report():
    """Export report as CSV or Excel"""
    from flask import send_file
    import pandas as pd
    import io
    
    report_type = request.args.get('type', 'monthly')
    format_type = request.args.get('format', 'csv')
    
    # Get data based on report type
    if report_type == 'monthly':
        report_data = monthly_report().get_json()
    else:
        report_data = annual_report().get_json()
    
    # Create DataFrame
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    df_data = []
    
    for tx in transactions:
        category = Category.query.get(tx.category_id)
        df_data.append({
            'Data': tx.timestamp.strftime('%Y-%m-%d %H:%M'),
            'Tipo': 'Receita' if tx.type == 'income' else 'Despesa',
            'Categoria': category.name if category else 'N/A',
            'Subcategoria': tx.subcategory_name or '',
            'Valor': tx.amount
        })
    
    df = pd.DataFrame(df_data)
    
    # Export based on format
    if format_type == 'csv':
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'relatorio_financeiro_{report_type}.csv'
        )
    elif format_type == 'excel':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Transações')
            
            # Add summary sheet
            summary_data = {
                'Métrica': ['Total Receitas', 'Total Despesas', 'Saldo Líquido'],
                'Valor': [
                    report_data['summary']['total_income'],
                    report_data['summary']['total_expenses'],
                    report_data['summary']['net_balance']
                ]
            }
            pd.DataFrame(summary_data).to_excel(writer, index=False, sheet_name='Resumo')
        
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'relatorio_financeiro_{report_type}.xlsx'
        )
    
    return jsonify({'error': 'Invalid format'}), 400


# Analytics API endpoints
@app.route('/api/analytics/spending')
@login_required
def analytics_spending():
    """Get detailed spending analytics"""
    from collections import defaultdict
    
    transactions = Transaction.query.filter_by(user_id=current_user.id, type='expense').all()
    
    # Monthly spending
    monthly_spending = defaultdict(float)
    daily_spending = defaultdict(float)
    category_spending = defaultdict(float)
    
    for tx in transactions:
        month_key = f"{tx.timestamp.year}-{tx.timestamp.month:02d}"
        day_key = tx.timestamp.strftime('%Y-%m-%d')
        
        monthly_spending[month_key] += tx.amount
        daily_spending[day_key] += tx.amount
        
        category = Category.query.get(tx.category_id)
        if category:
            category_spending[category.name] += tx.amount
    
    return jsonify({
        'monthly_spending': dict(monthly_spending),
        'daily_spending': dict(daily_spending),
        'category_spending': dict(category_spending),
        'total_spent': sum(monthly_spending.values()),
        'average_monthly': sum(monthly_spending.values()) / len(monthly_spending) if monthly_spending else 0
    })


@app.route('/api/analytics/trends')
@login_required
def analytics_trends():
    """Get spending trends and patterns"""
    from collections import defaultdict
    import calendar
    
    transactions = Transaction.query.filter_by(user_id=current_user.id, type='expense').all()
    
    # Day of week analysis
    day_of_week_spending = defaultdict(float)
    for tx in transactions:
        day_name = calendar.day_name[tx.timestamp.weekday()]
        day_of_week_spending[day_name] += tx.amount
    
    # Hour of day analysis
    hour_spending = defaultdict(float)
    for tx in transactions:
        hour_spending[tx.timestamp.hour] += tx.amount
    
    return jsonify({
        'day_of_week': dict(day_of_week_spending),
        'hour_of_day': dict(hour_spending),
        'insights': {
            'most_expensive_day': max(day_of_week_spending.items(), key=lambda x: x[1]) if day_of_week_spending else None,
            'most_expensive_hour': max(hour_spending.items(), key=lambda x: x[1]) if hour_spending else None
        }
    })


@app.route('/api/analytics/predictions')
@login_required
def analytics_predictions():
    """Get ML-based predictions (placeholder for future ML implementation)"""
    # This would use the ML models once implemented
    return jsonify({
        'message': 'ML predictions coming soon',
        'features': [
            'Spending forecasts',
            'Anomaly detection',
            'Category predictions',
            'Budget recommendations'
        ]
    })


# Dashboard Configuration API
@app.route('/api/dashboard/config', methods=['GET', 'POST'])
@login_required
def dashboard_config():
    """Get or update dashboard configuration"""
    if request.method == 'POST':
        data = request.json
        
        config = DashboardConfig.query.filter_by(user_id=current_user.id).first()
        if not config:
            config = DashboardConfig(user_id=current_user.id)
            db.session.add(config)
        
        if 'layout_config' in data:
            config.layout_config = json.dumps(data['layout_config'])
        if 'visible_widgets' in data:
            config.visible_widgets = json.dumps(data['visible_widgets'])
        if 'chart_preferences' in data:
            config.chart_preferences = json.dumps(data['chart_preferences'])
        if 'theme_color' in data:
            config.theme_color = data['theme_color']
        if 'default_chart_type' in data:
            config.default_chart_type = data['default_chart_type']
        
        config.updated_at = datetime.now()
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Dashboard configuration updated'})
    
    # GET request
    config = DashboardConfig.query.filter_by(user_id=current_user.id).first()
    
    if not config:
        # Return default configuration
        return jsonify({
            'layout_config': {
                'widgets': ['stats', 'charts', 'categories', 'transactions', 'goals', 'achievements']
            },
            'visible_widgets': ['stats', 'charts', 'categories', 'transactions'],
            'chart_preferences': {
                'default_type': 'pie',
                'colors': ['#2563eb', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6']
            },
            'theme_color': '#2563eb',
            'default_chart_type': 'pie'
        })
    
    return jsonify({
        'layout_config': json.loads(config.layout_config) if config.layout_config else {},
        'visible_widgets': json.loads(config.visible_widgets) if config.visible_widgets else [],
        'chart_preferences': json.loads(config.chart_preferences) if config.chart_preferences else {},
        'theme_color': config.theme_color,
        'default_chart_type': config.default_chart_type
    })


# Transaction API
@app.route('/api/transactions')
@login_required
def api_transactions():
    """Get all user transactions"""
    transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(
        Transaction.timestamp.desc()
    ).all()
    
    return jsonify([{
        'id': tx.id,
        'amount': tx.amount,
        'type': tx.type,
        'category_id': tx.category_id,
        'subcategory_name': tx.subcategory_name,
        'timestamp': tx.timestamp.isoformat()
    } for tx in transactions])


@app.route('/api/transaction', methods=['POST'])
@login_required
def api_create_transaction():
    """Create a new transaction"""
    data = request.json
    
    try:
        transaction = create_transaction(
            user_id=current_user.id,
            category_id=data['category_id'],
            amount=data['amount'],
            tx_type=data['type'],
            subcategory_name=data.get('subcategory_name'),
            timestamp=datetime.fromisoformat(data['timestamp']) if data.get('timestamp') else None
        )
        
        return jsonify({
            'success': True,
            'transaction_id': transaction.id,
            'message': 'Transaction created successfully'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/transaction/<int:transaction_id>', methods=['DELETE'])
@login_required
def api_delete_transaction(transaction_id):
    """Delete a transaction"""
    transaction = Transaction.query.get_or_404(transaction_id)
    
    if transaction.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db.session.delete(transaction)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Transaction deleted'})


if __name__ == '__main__':
    app.run(debug=True, port=5001)
