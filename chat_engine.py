"""Leitura de frases do chat para lançar receita ou despesa.

O resultado é só a intenção. Quem grava no banco é a aplicação, depois da
confirmação de quem está usando.
"""

from __future__ import annotations

import re
import unicodedata


def fold(text):
    raw = unicodedata.normalize('NFD', (text or '').lower())
    without = ''.join(ch for ch in raw if unicodedata.category(ch) != 'Mn')
    without = without.replace('€', ' euro ')
    without = re.sub(r'[^a-z0-9,.\s]', ' ', without)
    return re.sub(r'\s+', ' ', without).strip()


_INCOME = re.compile(
    r'\b(recebi|receber|ganhei|ganhar|entrada|salario|deposito|depositar|renda|adicionei|adicionar|entrou|receita)\b'
)
_EXPENSE = re.compile(
    r'\b(gastei|gastar|gasto|paguei|pagar|comprei|comprar|despesa|remover|retirei|saiu|debito)\b'
)
_AMOUNT = re.compile(
    r'(?:euro\s*|eur\s*|r\$\s*)?(\d+(?:[.,]\d{1,2})?)(?:\s*(?:euro|euros|eur|reais|r\$))?'
)
_CATEGORY = re.compile(
    r'\b(?:no|na|nos|nas|em|para|pra|de|do|da|num|numa|com)\s+([a-z][a-z0-9 ]{1,40})$'
)
_FILLER = {
    'euro', 'euros', 'eur', 'reais', 'dinheiro', 'real', 'conto', 'contos',
    'o', 'a', 'os', 'as', 'um', 'uma', 'meu', 'minha', 'hoje', 'ontem', 'amanha',
}


def _amount_value(raw):
    cleaned = raw.replace(' ', '')
    if ',' in cleaned and '.' not in cleaned:
        cleaned = cleaned.replace(',', '.')
    elif ',' in cleaned and '.' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')
    return float(cleaned)


def _clean_category(phrase):
    words = [word for word in phrase.split() if word not in _FILLER]
    while words and words[-1] in {'hoje', 'ontem', 'amanha', 'agora'}:
        words.pop()
    if not words:
        return None
    return ' '.join(words)


def _date_hint(text):
    if 'ontem' in text:
        return 'yesterday'
    if 'amanha' in text:
        return 'tomorrow'
    return 'today'


def _control_kind(text):
    if not text:
        return 'help'
    if text in {'oi', 'ola', 'hey', 'hello', 'hi', 'ajuda', 'help', 'menu', 'bom dia', 'boa tarde', 'boa noite'}:
        return 'help'
    if re.search(r'\b(o que voce faz|como funciona|como uso)\b', text):
        return 'help'
    if re.fullmatch(r'(sim|confirmar|confirma|confirmo|ok|isso|yes|pode|pode sim|pode registrar|pode registar)', text):
        return 'confirm'
    if re.search(r'\b(sim|confirmo|confirmar)\b', text) and not re.search(r'\d', text):
        return 'confirm'
    if re.search(r'\b(desfazer|undo|anular)\b', text):
        return 'undo'
    if re.search(r'\b(nao|cancelar|cancela|deixa pra la|melhor nao)\b', text) and not re.search(r'\d', text):
        return 'reject'
    return None


_CATEGORY_RAW = re.compile(
    r'(?:no|na|nos|nas|em|para|pra|de|do|da|num|numa|com)\s+(.+)$',
    re.IGNORECASE,
)


def parse_clause(clause, inherited_type=None):
    original = re.sub(r'\s+', ' ', (clause or '')).strip(' .')
    text = fold(original)
    text = re.sub(r'\b(hoje|ontem|amanha|agora)\b', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return None
    amount_match = _AMOUNT.search(text)
    if not amount_match:
        return None
    try:
        amount = _amount_value(amount_match.group(1))
    except ValueError:
        return None
    if amount <= 0:
        return None

    tx_type = inherited_type or 'expense'
    if _INCOME.search(text):
        tx_type = 'income'
    elif _EXPENSE.search(text):
        tx_type = 'expense'
    elif inherited_type is None:
        tx_type = 'expense'

    category = None
    category_match = _CATEGORY_RAW.search(original)
    if category_match:
        category = _clean_category(fold(category_match.group(1)))
        raw_words = category_match.group(1).strip()
        raw_clean = re.sub(
            r'\b(euro|euros|eur|reais|hoje|ontem|amanhã|amanha|agora)\b',
            ' ',
            raw_words,
            flags=re.IGNORECASE,
        )
        raw_clean = re.sub(r'\s+', ' ', raw_clean).strip(' .')
        if raw_clean:
            category = raw_clean
    return {
        'type': tx_type,
        'amount': round(amount, 2),
        'category': category,
        'date_hint': _date_hint(text),
    }


def interpret_message(message):
    text = fold(message)
    kind = _control_kind(text)
    if kind:
        return {'kind': kind, 'transactions': []}

    clauses = [part.strip() for part in re.split(r'\s+\be\b\s+|;\s*|\n+', message or '', flags=re.IGNORECASE) if part.strip()]
    transactions = []
    inherited = None
    for clause in clauses:
        parsed = parse_clause(clause, inherited)
        if not parsed:
            continue
        transactions.append(parsed)
        inherited = parsed['type']

    if not transactions:
        return {'kind': 'unknown', 'transactions': []}
    return {'kind': 'transactions', 'transactions': transactions}
