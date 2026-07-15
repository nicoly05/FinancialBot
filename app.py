from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from flask_sqlalchemy import SQLAlchemy
import bcrypt
from datetime import datetime
from config import Config
import json

app = Flask(__name__)
app.config.from_object(Config)

# SQLAlchemy setup
db = SQLAlchemy(app)


def init_database():
    with app.app_context():
        db.create_all()


init_database()

# Custom Jinja filter to parse JSON
@app.template_filter('from_json')
def from_json_filter(s):
    if s:
        try:
            return json.loads(s)
        except:
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

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    emoji = db.Column(db.String(10), default='📁')
    color = db.Column(db.String(7), default='#3b82f6')
    subcategories = db.Column(db.Text)  # JSON string
    created_at = db.Column(db.DateTime, default=datetime.now)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    subcategory = db.Column(db.String(200))
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(50), nullable=False)  # 'expense' or 'income'
    timestamp = db.Column(db.DateTime, default=datetime.now)

class ChatHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    sender = db.Column(db.String(50), nullable=False)  # 'user' or 'bot'
    timestamp = db.Column(db.DateTime, default=datetime.now)

@login_manager.user_loader
def load_user(user_id):
    init_database()
    return db.session.get(User, int(user_id))

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
            # Create or update session
            user_session = Session.query.filter_by(user_id=user.id).first()
            if not user_session:
                user_session = Session(user_id=user.id, categories_configured=user.categories_configured)
                db.session.add(user_session)
            else:
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

@app.route('/dashboard')
@login_required
def dashboard():
    user_categories = Category.query.filter_by(user_id=current_user.id).all()
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    
    # Create category name mapping
    category_names = {cat.id: cat.name for cat in user_categories}
    
    # Calculate totals (sum all transactions regardless of type)
    category_totals = {}
    total_spent = 0
    for cat in user_categories:
        cat_id = cat.id
        cat_total = sum(t.amount for t in transactions if t.category_id == cat_id)
        category_totals[cat_id] = cat_total
        total_spent += cat_total
    
    # Find category with highest spending
    highest_spending = None
    max_amount = 0
    for cat in user_categories:
        cat_id = cat.id
        if category_totals.get(cat_id, 0) > max_amount:
            max_amount = category_totals[cat_id]
            highest_spending = cat
    
    return render_template('dashboard.html', 
                          categories=user_categories,
                          transactions=transactions,
                          category_totals=category_totals,
                          total_spent=total_spent,
                          highest_spending=highest_spending,
                          category_names=category_names,
                          categories_json=[{
                              'id': cat.id,
                              'name': cat.name,
                              'emoji': cat.emoji,
                              'color': cat.color,
                              'subcategories': json.loads(cat.subcategories) if cat.subcategories else []
                          } for cat in user_categories])

@app.route('/chat')
@login_required
def chat():
    user_session = Session.query.filter_by(user_id=current_user.id).first()
    user_categories = Category.query.filter_by(user_id=current_user.id).all()
    chat_history = ChatHistory.query.filter_by(user_id=current_user.id).order_by(ChatHistory.timestamp.asc()).all()
    
    return render_template('chat.html', 
                          session=user_session,
                          categories=user_categories,
                          chat_history=chat_history)

@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    data = request.json
    user_message = data.get('message', '').strip().lower()
    
    # Save user message
    chat_msg = ChatHistory(
        user_id=current_user.id,
        message=user_message,
        sender='user',
        timestamp=datetime.now()
    )
    db.session.add(chat_msg)
    
    # Process message and get bot response
    response = process_chat_message(user_message, current_user.id)
    
    # Save bot response
    bot_msg = ChatHistory(
        user_id=current_user.id,
        message=response,
        sender='bot',
        timestamp=datetime.now()
    )
    db.session.add(bot_msg)
    db.session.commit()
    
    return jsonify({'response': response})

@app.route('/api/categories', methods=['GET'])
@login_required
def api_categories():
    categories = Category.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        'id': cat.id,
        'name': cat.name,
        'subcategories': json.loads(cat.subcategories) if cat.subcategories else []
    } for cat in categories])

@app.route('/api/category', methods=['POST'])
@login_required
def api_add_category():
    data = request.get_json()
    name = data.get('name')
    emoji = data.get('emoji', '📁')
    color = data.get('color', '#3b82f6')
    subcategories = data.get('subcategories', [])
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    # Check if category already exists
    existing = Category.query.filter_by(user_id=current_user.id, name=name).first()
    if existing:
        return jsonify({'error': 'Category already exists'}), 400
    
    # Limit subcategories to 10
    if len(subcategories) > 10:
        return jsonify({'error': 'Maximum 10 subcategories allowed'}), 400
    
    category = Category(
        user_id=current_user.id, 
        name=name, 
        emoji=emoji, 
        color=color, 
        subcategories=json.dumps(subcategories) if subcategories else None
    )
    db.session.add(category)
    
    # Update session to mark categories as configured
    user_session = Session.query.filter_by(user_id=current_user.id).first()
    if user_session:
        user_session.categories_configured = True
    else:
        user_session = Session(user_id=current_user.id, categories_configured=True)
        db.session.add(user_session)
    
    db.session.commit()
    
    return jsonify({'success': True, 'id': category.id})

@app.route('/api/category/<int:category_id>/check', methods=['GET'])
@login_required
def api_check_category(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404
    
    transactions = Transaction.query.filter_by(category_id=category_id).first()
    has_transactions = transactions is not None
    
    return jsonify({'has_transactions': has_transactions})

@app.route('/api/category/<int:category_id>/move', methods=['POST'])
@login_required
def api_move_transactions(category_id):
    data = request.get_json()
    target_id = data.get('target_id')
    
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404
    
    target = Category.query.get(target_id)
    if not target or target.user_id != current_user.id:
        return jsonify({'error': 'Target category not found'}), 404
    
    # Move all transactions to target category
    transactions = Transaction.query.filter_by(category_id=category_id).all()
    for transaction in transactions:
        transaction.category_id = target_id
    
    # Delete the category
    db.session.delete(category)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/category/<int:category_id>', methods=['GET'])
@login_required
def api_get_category(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404
    
    return jsonify({
        'id': category.id,
        'name': category.name,
        'emoji': category.emoji,
        'color': category.color,
        'subcategories': json.loads(category.subcategories) if category.subcategories else []
    })

@app.route('/api/category/<int:category_id>', methods=['PUT'])
@login_required
def api_update_category(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404
    
    data = request.get_json()
    name = data.get('name')
    emoji = data.get('emoji', '📁')
    color = data.get('color', '#3b82f6')
    subcategories = data.get('subcategories')
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    # Check if name already exists for another category
    existing = Category.query.filter_by(user_id=current_user.id, name=name).first()
    if existing and existing.id != category_id:
        return jsonify({'error': 'Category name already exists'}), 400
    
    # Limit subcategories to 10 if provided
    if subcategories is not None and len(subcategories) > 10:
        return jsonify({'error': 'Maximum 10 subcategories allowed'}), 400
    
    category.name = name
    category.emoji = emoji
    category.color = color
    if subcategories is not None:
        category.subcategories = json.dumps(subcategories) if subcategories else None
    
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/category/<int:category_id>', methods=['DELETE'])
@login_required
def api_delete_category(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404
    
    # Delete the category
    db.session.delete(category)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/category/<int:category_id>/subcategory', methods=['POST'])
@login_required
def api_add_subcategory(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404
    
    data = request.get_json()
    name = data.get('name')
    
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    
    subcategories = json.loads(category.subcategories) if category.subcategories else []
    if name in subcategories:
        return jsonify({'error': 'Subcategory already exists'}), 400
    
    subcategories.append(name)
    category.subcategories = json.dumps(subcategories)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/category/<int:category_id>/subcategory', methods=['DELETE'])
@login_required
def api_delete_subcategory(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404
    
    data = request.get_json()
    index = data.get('index')
    
    if index is None:
        return jsonify({'error': 'Index is required'}), 400
    
    subcategories = json.loads(category.subcategories) if category.subcategories else []
    if 0 <= index < len(subcategories):
        subcategories.pop(index)
        category.subcategories = json.dumps(subcategories)
        db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/category/<int:category_id>/subcategory-totals', methods=['GET'])
@login_required
def api_subcategory_totals(category_id):
    category = Category.query.get(category_id)
    if not category or category.user_id != current_user.id:
        return jsonify({'error': 'Category not found'}), 404

    subcategories = json.loads(category.subcategories) if category.subcategories else []
    transactions = Transaction.query.filter_by(category_id=category_id).all()

    totals = {sub: 0 for sub in subcategories}
    no_subcategory_total = 0
    for t in transactions:
        if t.subcategory and t.subcategory in totals:
            totals[t.subcategory] += t.amount
        else:
            no_subcategory_total += t.amount

    return jsonify({
        'category_id': category.id,
        'category_name': category.name,
        'category_color': category.color,
        'subcategories': [{'name': s, 'total': totals[s]} for s in subcategories],
        'no_subcategory_total': no_subcategory_total
    })

@app.route('/api/dashboard-data', methods=['GET'])
@login_required
def api_dashboard_data():
    user_categories = Category.query.filter_by(user_id=current_user.id).all()
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    
    category_totals = {}
    for cat in user_categories:
        cat_id = cat.id
        cat_total = sum(t.amount for t in transactions if t.category_id == cat_id and t.type == 'expense')
        category_totals[cat.name] = cat_total
    
    return jsonify({
        'categories': [cat.name for cat in user_categories],
        'totals': category_totals,
        'total_spent': sum(category_totals.values())
    })

NO_CATEGORIES_MESSAGE = (
    "✨ Bem-vindo(a) ao teu bot de finanças! ✨\n\n"
    "Antes de começares, precisas de ter pelo menos uma categoria criada. É rápido:\n\n"
    "1️⃣ Vai ao Dashboard\n"
    "2️⃣ Na secção \"Categorias\", clica em \"Editar\"\n"
    "3️⃣ Clica em \"Adicionar\", escolhe um nome, emoji e cor (e subcategorias, se quiseres)\n"
    "4️⃣ Clica em \"Guardar\"\n\n"
    "Depois de teres pelo menos uma categoria, volta aqui e usa o chat para registar valores:\n\n"
    "💰 \"adicionar 50\" — para somar 50€ a uma categoria (ex: um rendimento)\n"
    "➖ \"remover 20\" — para subtrair 20€ de uma categoria (ex: uma despesa)\n\n"
    "Eu pergunto sempre em que categoria (e subcategoria, se a categoria tiver) queres aplicar o valor."
)

CHAT_HELP_MESSAGE = (
    "Já tens categorias configuradas! 🙂\n\n"
    "Podes gerir mais categorias e subcategorias a qualquer momento no Dashboard, "
    "na secção \"Categorias\" → botão \"Editar\".\n\n"
    "Aqui no chat, usa:\n\n"
    "💰 \"adicionar 50\" — para somar um valor a uma categoria\n"
    "➖ \"remover 20\" — para subtrair um valor de uma categoria\n\n"
    "Depois do comando, escolho contigo a categoria (e a subcategoria, se existir)."
)

def process_chat_message(message, user_id):
    # Check if user has categories configured
    user_categories = Category.query.filter_by(user_id=user_id).all()
    if not user_categories:
        return NO_CATEGORIES_MESSAGE
    
    # Handle value modification
    return handle_value_modification(message, user_id)


def handle_category_configuration(message, user_id):
    user_session = Session.query.filter_by(user_id=user_id).first()
    
    # Check if user already has categories configured
    existing_categories = Category.query.filter_by(user_id=user_id).all()
    if existing_categories:
        # Mark session as configured
        if not user_session:
            user_session = Session(user_id=user_id)
            db.session.add(user_session)
        user_session.categories_configured = True
        db.session.commit()
        return "Já tens categorias configuradas! Podes adicionar ou remover valores. Escreve \"adicionar\" ou \"remover\" seguido do valor em €. Exemplo: adicionar 400"
    
    # Initial greeting
    if message in ['oi', 'olá', 'hello', 'hi']:
        return """"✨ Bem-vindo(a) ao teu bot de finanças! ✨\n\n"
    "Antes de começares, precisas de ter pelo menos uma categoria criada. É rápido:\n\n"
    "1️⃣ Vai ao Dashboard\n"
    "2️⃣ Na secção \"Categorias\", clica em \"Editar\"\n"
    "3️⃣ Clica em \"Adicionar\", escolhe um nome, emoji e cor (e subcategorias, se quiseres)\n"
    "4️⃣ Clica em \"Guardar\"\n\n"
    "Depois de teres pelo menos uma categoria, volta aqui e usa o chat para registar valores:\n\n"
    "💰 \"adicionar 50\" — para somar 50€ a uma categoria (ex: um rendimento)\n"
    "➖ \"remover 20\" — para subtrair 20€ de uma categoria (ex: uma despesa)\n\n"
    "Eu pergunto sempre em que categoria (e subcategoria, se a categoria tiver) queres aplicar o valor."
"""
    
    # Parse categories
    if message.startswith('1.') or message.startswith('2.') or message.startswith('3.'):
        lines = message.split('\n')
        categories = []
        for line in lines:
            if line.strip():
                parts = line.split('.')
                if len(parts) >= 2:
                    categories.append(parts[1].strip())
        
        if categories:
            # Save categories
            for cat_name in categories:
                category = Category(
                    user_id=user_id,
                    name=cat_name,
                    subcategories=json.dumps([])
                )
                db.session.add(category)
            
            if not user_session:
                user_session = Session(user_id=user_id)
                db.session.add(user_session)
            
            user_session.pending_subcategories = json.dumps(categories)
            db.session.commit()
            
            return "Desejas adicionar subcategorias? Se sim indica o número da categoria."
    
    # Handle subcategory selection or skip
    if user_session and user_session.pending_subcategories:
        # Check if user wants to skip subcategories
        if message in ['não', 'nao', 'no', 'n']:
            user_session.pending_subcategories = None
            user_session.selected_category_for_sub = None
            # Mark categories as configured
            user_session.categories_configured = True
            user = User.query.get(user_id)
            if user:
                user.categories_configured = True
            db.session.commit()
            return "✅ Configuração guardada! Agora podes adicionar ou remover valores. Escreve \"adicionar\" ou \"remover\" seguido do valor em €. Exemplo: adicionar 400"
        
        # Handle subcategory selection
        if message.isdigit():
            cat_index = int(message) - 1
            pending_cats = json.loads(user_session.pending_subcategories)
            
            if 0 <= cat_index < len(pending_cats):
                selected_cat = pending_cats[cat_index]
                user_session.selected_category_for_sub = selected_cat
                db.session.commit()
                return f"Indica as subcategorias para \"{selected_cat}\"...\n\n1. subcategoria 1\n2. subcategoria 2\n3. subcategoria 3"
    
    # Parse subcategories
    if (message.startswith('1.') or message.startswith('2.') or message.startswith('3.')) and user_session and user_session.selected_category_for_sub:
        lines = message.split('\n')
        subcategories = []
        for line in lines:
            if line.strip():
                parts = line.split('.')
                if len(parts) >= 2:
                    subcategories.append(parts[1].strip())
        
        if subcategories:
            cat_name = user_session.selected_category_for_sub
            category = Category.query.filter_by(user_id=user_id, name=cat_name).first()
            if category:
                category.subcategories = json.dumps(subcategories)
            
            user_session.selected_category_for_sub = None
            user_session.categories_configured = True
            user = User.query.get(user_id)
            if user:
                user.categories_configured = True
            db.session.commit()
            
            return "✅ Configuração guardada! Agora podes adicionar ou remover valores. Escreve \"adicionar\" ou \"remover\" seguido do valor em €. Exemplo: adicionar 400"
    
    return "Não entendi. Por favor, segue as instruções anteriores."

def handle_value_modification(message, user_id):
    user_session = Session.query.filter_by(user_id=user_id).first()
    
    # Saudação ou pedido de ajuda: recorda como usar o dashboard e o chat
    if message in ['oi', 'olá', 'ola', 'hello', 'hi', 'ajuda', 'help', 'como funciona']:
        return CHAT_HELP_MESSAGE
    
    # Check for add/remove command
    if message.startswith('adicionar'):
        try:
            amount = float(message.split()[1])
            user_session.pending_action = 'add'
            user_session.pending_amount = amount
            db.session.commit()
            
            user_categories = Category.query.filter_by(user_id=user_id).all()
            cat_list = "\n".join([f"{i+1}. {cat.name}" for i, cat in enumerate(user_categories)])
            return f"Em que categoria queres adicionar {amount}€?\n\n{cat_list}"
        except (IndexError, ValueError):
            return "Formato incorreto. Exemplo: adicionar 400"
    
    if message.startswith('remover'):
        try:
            amount = float(message.split()[1])
            user_session.pending_action = 'remove'
            user_session.pending_amount = amount
            db.session.commit()
            
            user_categories = Category.query.filter_by(user_id=user_id).all()
            cat_list = "\n".join([f"{i+1}. {cat.name}" for i, cat in enumerate(user_categories)])
            return f"Em que categoria queres remover {amount}€?\n\n{cat_list}"
        except (IndexError, ValueError):
            return "Formato incorreto. Exemplo: remover 400"
    
    # Handle subcategory selection (tem de vir ANTES da seleção de categoria,
    # senão o número da subcategoria é interpretado como número de categoria)
    if message.isdigit() and user_session and user_session.selected_category:
        cat = Category.query.get(user_session.selected_category)
        if cat:
            subcategories = json.loads(cat.subcategories) if cat.subcategories else []
            if subcategories:
                sub_index = int(message) - 1
                if 0 <= sub_index < len(subcategories):
                    selected_sub = subcategories[sub_index]
                    action = user_session.pending_action
                    amount = user_session.pending_amount
                    
                    transaction_type = 'income' if action == 'add' else 'expense'
                    # Use negative amount for remove action
                    transaction_amount = amount if action == 'add' else -amount
                    transaction = Transaction(
                        user_id=user_id,
                        category_id=cat.id,
                        subcategory=selected_sub,
                        amount=transaction_amount,
                        type=transaction_type,
                        timestamp=datetime.now()
                    )
                    db.session.add(transaction)
                    
                    user_session.pending_action = None
                    user_session.pending_amount = None
                    user_session.selected_category = None
                    db.session.commit()
                    
                    return f"✅ {amount}€ {'adicionado a' if action == 'add' else 'removido de'} {selected_sub} ({cat.name})!"
    
    # Handle category selection (só dispara se não estivermos a meio da escolha de subcategoria)
    if message.isdigit() and user_session and user_session.pending_action and not user_session.selected_category:
        cat_index = int(message) - 1
        user_categories = Category.query.filter_by(user_id=user_id).all()
        
        if 0 <= cat_index < len(user_categories):
            selected_cat = user_categories[cat_index]
            action = user_session.pending_action
            amount = user_session.pending_amount
            
            # Check for subcategories
            subcategories = json.loads(selected_cat.subcategories) if selected_cat.subcategories else []
            if subcategories:
                sub_list = "\n".join([f"{i+1}. {sub}" for i, sub in enumerate(subcategories)])
                user_session.selected_category = selected_cat.id
                db.session.commit()
                return f"A categoria {selected_cat.name} tem subcategorias: {', '.join(subcategories)}.\nQual delas queres alterar?\n\n{sub_list}"
            
            # No subcategories, add transaction directly
            transaction_type = 'income' if action == 'add' else 'expense'
            # Use negative amount for remove action
            transaction_amount = amount if action == 'add' else -amount
            transaction = Transaction(
                user_id=user_id,
                category_id=selected_cat.id,
                subcategory=None,
                amount=transaction_amount,
                type=transaction_type,
                timestamp=datetime.now()
            )
            db.session.add(transaction)
            
            user_session.pending_action = None
            user_session.pending_amount = None
            db.session.commit()
            
            return f"✅ {amount}€ {'adicionado a' if action == 'add' else 'removido de'} {selected_cat.name}!"
    
    return "Comando não reconhecido. Escreve \"adicionar\" ou \"remover\" seguido do valor em €, ou escreve \"ajuda\" para ver as instruções."

if __name__ == '__main__':
    app.run(debug=True, port=5001)