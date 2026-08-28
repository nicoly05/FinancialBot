from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate, upgrade
import bcrypt
import json
import os
import re
from datetime import datetime, timedelta
from config import Config 
import requests

app = Flask(__name__)
app.config.from_object(Config)

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
    due_day = db.Column(db.Integer, nullable=False)  # Dia do mês (1-31)
    last_paid_month = db.Column(db.Integer)  # Mês em que foi pago pela última vez
    last_paid_year = db.Column(db.Integer)  # Ano em que foi pago pela última vez
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

        current_user.email = email
        if profile_photo:
            current_user.profile_photo = profile_photo

        db.session.commit()
        flash('Perfil atualizado com sucesso!', 'success')
        return redirect(url_for('profile'))

    return render_template('profile.html', user=current_user)


@app.route('/dashboard')
@login_required
def dashboard():
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
        else:
            bill.last_paid_month = None
            bill.last_paid_year = None
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.context_processor
def inject_now():
    return {'now': datetime.now}
    
NO_CATEGORIES_MESSAGE = (
    "✨ Bem-vindo(a), {username}! ✨\n\n"
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


def process_chat_message(message, user_id):
    user_categories = _get_user_categories(user_id)
    if not user_categories:
        user = User.query.get(user_id)
        username = user.username if user else "utilizador"
        return NO_CATEGORIES_MESSAGE.format(username=username)
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


if __name__ == '__main__':
    app.run(debug=True, port=5002)
