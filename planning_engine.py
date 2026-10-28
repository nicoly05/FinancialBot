"""Distribuição financeira a partir do que a pessoa escreveu.

O plano deixa de ser um molde fixo: percentuais explícitos, sonhos, gastos
listados, metas e o texto de organização mudam a fatia de cada caixinha.
"""

from __future__ import annotations

import re
import unicodedata


BUCKETS = (
    'security',
    'fixed_expenses',
    'personal_investment',
    'travel',
    'big_goals',
)

LABELS = {
    'security': 'Segurança / Futuro',
    'fixed_expenses': 'Gastos fixos',
    'personal_investment': 'Investimento pessoal',
    'travel': 'Viagens',
    'big_goals': 'Grandes objetivos',
}

EMOJIS = {
    'security': '🛡️',
    'fixed_expenses': '🏠',
    'personal_investment': '🌱',
    'travel': '✈️',
    'big_goals': '🏡',
}

# Aliases já sem acento. O mais longo de cada caixinha vence dentro dela.
ALIASES = {
    'travel': [
        'viagem para paris', 'viagens', 'viagem', 'viajar', 'ferias', 'turismo', 'paris',
    ],
    'big_goals': [
        'grandes objetivos', 'casa propria', 'comprar casa', 'comprar a casa', 'entrada da casa',
        'imovel', 'casamento', 'negocio proprio', 'negocio', 'reforma', 'carro', 'casa',
    ],
    'security': [
        'reserva de emergencia', 'fundo de emergencia', 'independencia financeira',
        'aposentadoria', 'previdencia', 'emergencia', 'seguranca', 'reserva', 'futuro',
    ],
    'personal_investment': [
        'investimento pessoal', 'sem culpa', 'qualidade de vida', 'faculdade', 'estudos',
        'curso', 'lazer', 'hobby', 'academia', 'beleza', 'pessoal', 'livros',
    ],
    'fixed_expenses': [
        'despesas fixas', 'gastos fixos', 'gasto fixo', 'contas da casa', 'supermercado',
        'moradia', 'aluguel', 'internet', 'condominio', 'mercado', 'contas', 'luz', 'agua',
    ],
}

PRIORITY_BUCKET = {
    'emergency': 'security',
    'retirement': 'security',
    'investments': 'security',
    'independence': 'security',
    'house': 'big_goals',
    'car': 'big_goals',
    'business': 'big_goals',
    'wedding': 'big_goals',
    'travel': 'travel',
    'education': 'personal_investment',
    'health': 'personal_investment',
    'hobbies': 'personal_investment',
    'technology': 'personal_investment',
    'family': 'fixed_expenses',
    'home_improvement': 'big_goals',
}

EXPENSE_WORDS = (
    'aluguel', 'mercado', 'supermercado', 'luz', 'agua', 'internet', 'condominio',
    'transporte', 'gasolina', 'escola', 'farmacia', 'plano de saude',
)


def fold(text):
    raw = unicodedata.normalize('NFD', (text or '').lower())
    without = ''.join(ch for ch in raw if unicodedata.category(ch) != 'Mn')
    without = without.replace('€', ' euro ')
    without = re.sub(r'[^a-z0-9%.,\s]', ' ', without)
    return re.sub(r'\s+', ' ', without).strip()


def _money(value):
    return f'€{float(value):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _to_float(value, default=0.0):
    if value is None or value == '':
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace(' ', '')
    if ',' in raw and '.' in raw:
        raw = raw.replace('.', '').replace(',', '.')
    else:
        raw = raw.replace(',', '.')
    try:
        return float(raw)
    except ValueError:
        return float(default)


def buckets_in(phrase):
    folded = fold(phrase)
    if not folded:
        return []
    best = {}
    for bucket, aliases in ALIASES.items():
        for alias in aliases:
            if alias in folded and len(alias) > best.get(bucket, (0, ''))[0]:
                best[bucket] = (len(alias), alias)
    kept = []
    items = list(best.items())
    for bucket, (length, alias) in items:
        covered = False
        for other, (other_len, other_alias) in items:
            if other != bucket and alias in other_alias and other_len > length:
                covered = True
                break
        if not covered:
            kept.append((length, bucket))
    kept.sort(reverse=True)
    return [bucket for _, bucket in kept]


def _base_weights(data):
    weights = {
        'security': 16.0,
        'fixed_expenses': 42.0,
        'personal_investment': 14.0,
        'travel': 12.0,
        'big_goals': 16.0,
    }
    status = (data.get('emergency_fund_status') or 'none').strip()
    if status in {'none', 'less_1_month', ''}:
        weights['security'] += 14
    elif status == 'more_6_months':
        weights['security'] = max(8, weights['security'] - 6)
    if data.get('urgent_debt') or (_to_float(data.get('total_debt')) > 0 and data.get('has_debts')):
        weights['security'] += 10
    if (data.get('income_type') or '') in {'variavel', 'mista'}:
        weights['security'] += 6
    dependents = int(_to_float(data.get('dependents')))
    if dependents:
        weights['fixed_expenses'] += min(dependents, 5) * 3
    if data.get('shares_expenses'):
        weights['fixed_expenses'] = max(12, weights['fixed_expenses'] - 8)
    try:
        age = int(_to_float(data.get('current_age'), 30))
        retire = int(_to_float(data.get('retirement_age'), 65))
    except (TypeError, ValueError):
        age, retire = 30, 65
    if retire - age <= 15:
        weights['security'] += 8
    return weights


def _collect_text(data):
    parts = []
    for key, label in (
        ('organization_notes', 'organização'),
        ('dreams', 'sonhos'),
        ('observation', 'observação'),
    ):
        text = (data.get(key) or '').strip()
        if text:
            parts.append((label, text))
    labels = data.get('priority_labels') or []
    if isinstance(labels, str):
        labels = [labels]
    custom = [str(item).strip() for item in labels if str(item).strip()]
    if custom:
        parts.append(('prioridades', ', '.join(custom)))
    priorities = data.get('priorities') or []
    if isinstance(priorities, str):
        priorities = [priorities]
    written_keys = []
    for item in priorities:
        if isinstance(item, dict):
            label = (item.get('label') or item.get('key') or '').strip()
        else:
            label = str(item).replace('_', ' ').strip()
        if label and label not in PRIORITY_BUCKET and label.lower() != '[]':
            written_keys.append(label)
    if written_keys:
        parts.append(('prioridades', ', '.join(written_keys)))
    return parts


def _apply_written_preferences(data, weights, locks, explanations, signals):
    pieces = _collect_text(data)
    full = ' '.join(fold(text) for _, text in pieces)
    if not full:
        return ''

    excerpt_source = next((text for label, text in pieces if label == 'organização'), '')
    if not excerpt_source:
        excerpt_source = pieces[0][1]
    excerpt = excerpt_source.strip()

    for label, text in pieces:
        signals.append({'label': f'Texto de {label}', 'detail': text.strip()[:180]})

    sentences = [part.strip() for part in re.split(r'[.!?\n]+', full) if part.strip()]

    for sentence in sentences:
        found = buckets_in(sentence)
        if not found:
            continue
        if re.search(r'\b(nao quero|nao desejo|quase nada|nada de|sem |minimo|zero|pouco para|pouco em)\b', sentence):
            for bucket in found:
                locks[bucket] = min(locks.get(bucket, 100), 3)
                explanations.append(
                    f'Você pediu pouco ou nada para {LABELS[bucket].lower()}, então deixei essa caixinha pequena.'
                )
                signals.append({'label': LABELS[bucket], 'detail': 'pedido de reduzir no texto'})
        elif re.search(r'\b(prioriz\w*|foco|focando|principal|mais importante|quero muito|o maximo)\b', sentence):
            for bucket in found:
                weights[bucket] += 36
                explanations.append(
                    f'O texto coloca {LABELS[bucket].lower()} como foco, então essa fatia cresceu.'
                )
                signals.append({'label': LABELS[bucket], 'detail': 'prioridade escrita'})
        elif re.search(r'\bmetade\b', sentence):
            for bucket in found[:1]:
                locks[bucket] = 50
                explanations.append(f'Você falou em metade da renda para {LABELS[bucket].lower()}.')
        elif re.search(r'\bum terco\b', sentence):
            for bucket in found[:1]:
                locks[bucket] = 33
                explanations.append(f'Você falou em um terço da renda para {LABELS[bucket].lower()}.')
        elif not re.search(r'\d+\s*(?:%|por cento)', sentence):
            for bucket in found:
                weights[bucket] += 32
                explanations.append(
                    f'O texto cita {LABELS[bucket].lower()}, então essa caixinha ficou maior do que no ponto de partida.'
                )
                signals.append({'label': LABELS[bucket], 'detail': 'citado no texto'})

    percent_re = re.compile(
        r'(\d{1,3}(?:[.,]\d+)?)\s*(?:%|por cento)\s*(?:da renda\s*)?(?:para|pra|em|na|no|de)?\s*'
        r'([a-z][a-z0-9 ]{2,40}?)(?=\s+e\s+|\s*,|\s*\.|$)'
    )
    for match in percent_re.finditer(full):
        pct = _to_float(match.group(1))
        if pct <= 0 or pct > 100:
            continue
        phrase = match.group(2)
        found = buckets_in(phrase)
        if not found:
            explanations.append(f'Vi {pct:.0f}% no texto, mas não associei "{phrase.strip()}" a uma caixinha.')
            continue
        bucket = found[0]
        locks[bucket] = pct
        explanations.append(
            f'Você escreveu {pct:.0f}% para {LABELS[bucket].lower()} e eu usei esse percentual.'
        )
        signals.append({'label': LABELS[bucket], 'detail': f'{pct:.0f}% pedidos no texto'})

    amount_re = re.compile(
        r'(?:guardar|separar|colocar|destinar|quero)\s*(?:euro|euros|eur|r\$)?\s*'
        r'(\d+(?:[.,]\d+)?)\s*(?:euro|euros|eur|reais)?\s*(?:para|pra|em|na|no)\s+'
        r'([a-z][a-z0-9 ]{2,40}?)(?=\s+e\s+|\s*,|\s*\.|$)'
    )
    income = _to_float(data.get('monthly_income')) + _to_float(data.get('other_income'))
    for match in amount_re.finditer(full):
        amount = _to_float(match.group(1))
        found = buckets_in(match.group(2))
        if not found or income <= 0 or amount <= 0:
            continue
        bucket = found[0]
        if bucket in locks:
            continue
        pct = amount / income * 100
        locks[bucket] = min(80, pct)
        explanations.append(
            f'Você quer separar {_money(amount)} para {LABELS[bucket].lower()} '
            f'({pct:.0f}% da renda).'
        )
        signals.append({'label': LABELS[bucket], 'detail': f'{_money(amount)} pedidos no texto'})

    # Gastos nomeados dentro do texto quando a pessoa não lançou linha a linha.
    if not data.get('expenses'):
        for word in EXPENSE_WORDS:
            named = re.search(
                rf'\b{word}\b\s*(?:de|por|:|-)?\s*(?:euro|euros|eur)?\s*(\d+(?:[.,]\d+)?)',
                full,
            )
            if not named:
                named = re.search(
                    rf'(\d+(?:[.,]\d+)?)\s*(?:euro|euros|eur)?\s*(?:de|com|em|para)?\s*\b{word}\b',
                    full,
                )
            if named:
                amount = _to_float(named.group(1))
                if amount > 0:
                    data.setdefault('_text_expenses', []).append({'name': word, 'amount': amount})
                    signals.append({'label': 'Gasto escrito', 'detail': f'{word} {_money(amount)}'})

    return excerpt


def _priority_keys(data):
    raw = data.get('priorities') or []
    if isinstance(raw, str):
        raw = [raw]
    keys = []
    for item in raw:
        if isinstance(item, dict):
            key = str(item.get('key') or item.get('label') or '')
        else:
            key = str(item)
        key = key.strip()
        if key and key != '[]':
            keys.append(key)
    return keys


def _round_to_100(raw, protected):
    floors = {key: int(round(raw.get(key, 0))) for key in BUCKETS}
    # int(round) can drift; correct toward the protected locks first by
    # adjusting the unlocked bucket with the largest remainder.
    drift = 100 - sum(floors.values())
    remainders = sorted(
        BUCKETS,
        key=lambda key: (raw.get(key, 0) - int(raw.get(key, 0)), raw.get(key, 0)),
        reverse=True,
    )
    guard = 0
    while drift != 0 and guard < 12:
        guard += 1
        if drift > 0:
            target = next((key for key in remainders if key not in protected), remainders[0])
            floors[target] += 1
            drift -= 1
        else:
            candidates = [key for key in BUCKETS if key not in protected and floors[key] > 0]
            if not candidates:
                candidates = [key for key in BUCKETS if floors[key] > 0]
            if not candidates:
                break
            target = max(candidates, key=lambda key: floors[key])
            floors[target] -= 1
            drift += 1
    return floors


def _allocate(weights, locks, minimums):
    locked = {bucket: max(0.0, min(100.0, value)) for bucket, value in locks.items()}
    locked_sum = sum(locked.values())
    if locked_sum > 100 and locked_sum > 0:
        scale = 100 / locked_sum
        locked = {bucket: value * scale for bucket, value in locked.items()}
        locked_sum = 100.0

    remaining = 100.0 - locked_sum
    unlocked = [bucket for bucket in BUCKETS if bucket not in locked]
    mins = {bucket: max(0.0, minimums.get(bucket, 0.0)) for bucket in unlocked}
    min_sum = sum(mins.values())
    if min_sum > remaining and min_sum > 0:
        scale = remaining / min_sum
        mins = {bucket: value * scale for bucket, value in mins.items()}
        min_sum = remaining

    leftover = max(0.0, remaining - min_sum)
    weight_sum = sum(max(weights.get(bucket, 1.0), 0.1) for bucket in unlocked) or 1
    result = {bucket: 0.0 for bucket in BUCKETS}
    result.update(locked)
    for bucket in unlocked:
        share = leftover * (max(weights.get(bucket, 1.0), 0.1) / weight_sum)
        result[bucket] = mins[bucket] + share
    return result


def _empty_plan(message):
    zeros = {bucket: 0 for bucket in BUCKETS}
    return {
        'monthly_income': 0,
        'total_income': 0,
        'security_percentage': 0,
        'fixed_expenses_percentage': 0,
        'personal_investment_percentage': 0,
        'travel_percentage': 0,
        'big_goals_percentage': 0,
        'security_amount': 0,
        'fixed_expenses_amount': 0,
        'personal_investment_amount': 0,
        'travel_amount': 0,
        'big_goals_amount': 0,
        'headline': 'Falta a renda para montar o plano',
        'read_excerpt': '',
        'explanations': [message],
        'alerts': [message],
        'signals': [],
        'boxes': [],
        'checklist': [],
        'selected_priorities': [],
        'priorities_count': 0,
        'ai_summary': None,
        'main_priority': '',
    }


def compute_financial_plan(data):
    data = dict(data or {})
    income = _to_float(data.get('monthly_income')) + _to_float(data.get('other_income'))
    if income <= 0:
        return _empty_plan('Informe uma renda maior que zero para eu distribuir o dinheiro.')

    weights = _base_weights(data)
    locks = {}
    explanations = []
    alerts = []
    signals = []

    excerpt = _apply_written_preferences(data, weights, locks, explanations, signals)

    for key in _priority_keys(data):
        bucket = PRIORITY_BUCKET.get(key)
        if not bucket:
            found = buckets_in(key.replace('_', ' '))
            bucket = found[0] if found else None
        if bucket:
            weights[bucket] += 18
            signals.append({'label': LABELS[bucket], 'detail': f'prioridade marcada ({key})'})

    expenses = []
    for item in data.get('expenses') or []:
        name = str(item.get('name') or '').strip()
        amount = _to_float(item.get('amount'))
        if name and amount > 0:
            expenses.append({'name': name, 'amount': amount, 'category': item.get('category') or ''})
    for item in data.get('_text_expenses') or []:
        expenses.append({'name': item['name'], 'amount': item['amount'], 'category': 'texto'})

    listed = sum(item['amount'] for item in expenses)
    debt_month = _to_float(data.get('monthly_debt_payment')) if data.get('has_debts') else 0
    fixed_euros = listed + max(debt_month, 0)
    minimums = {bucket: 0.0 for bucket in BUCKETS}
    if fixed_euros > 0:
        minimums['fixed_expenses'] = fixed_euros / income * 100
        if listed:
            explanations.append(
                f'Os gastos que você listou somam {_money(listed)} '
                f'({listed / income * 100:.0f}% da renda de {_money(income)}).'
            )
        if debt_month:
            explanations.append(
                f'A parcela das dívidas ({_money(debt_month)}) entrou junto dos gastos fixos.'
            )

    lifestyle = _to_float(data.get('lifestyle_budget'))
    if lifestyle > 0:
        minimums['personal_investment'] = max(
            minimums['personal_investment'], lifestyle / income * 100
        )
        explanations.append(
            f'O valor para viver sem culpa ({_money(lifestyle)}) foi para investimento pessoal / lazer.'
        )

    goals = []
    for item in data.get('goals') or []:
        name = str(item.get('name') or '').strip()
        amount = _to_float(item.get('amount') or item.get('target_amount'))
        months = int(_to_float(item.get('deadline') or item.get('months') or 12))
        current = _to_float(item.get('current_amount') or item.get('current') or 0)
        if not name or amount <= 0:
            continue
        months = max(months, 1)
        monthly = max(amount - current, 0) / months
        bucket = None
        category = str(item.get('category') or '')
        if category in PRIORITY_BUCKET:
            bucket = PRIORITY_BUCKET[category]
        else:
            found = buckets_in(f'{category} {name}')
            bucket = found[0] if found else 'big_goals'
        goals.append({
            'name': name,
            'category': category,
            'bucket': bucket,
            'amount': amount,
            'current': current,
            'months': months,
            'monthly': monthly,
        })
        if bucket not in locks:
            minimums[bucket] += monthly / income * 100
        explanations.append(
            f'A meta "{name}" pede cerca de {_money(monthly)} por mês '
            f'({LABELS[bucket].lower()}, {months} meses).'
        )
        if monthly > income * 0.5:
            alerts.append(
                f'A meta "{name}" sozinha pediria {_money(monthly)} por mês, '
                f'mais da metade da renda. O plano reparte o que cabe agora.'
            )

    if 'fixed_expenses' in locks and fixed_euros > 0:
        needed = fixed_euros / income * 100
        if locks['fixed_expenses'] + 0.5 < needed:
            explanations.append(
                f'Você sugeriu {locks["fixed_expenses"]:.0f}% para gastos fixos, '
                f'mas o que está escrito soma cerca de {needed:.0f}%. Mantive o valor real das contas.'
            )
            locks['fixed_expenses'] = needed

    raw = _allocate(weights, locks, minimums)
    protected = set(locks)
    if fixed_euros > 0:
        protected.add('fixed_expenses')
    percentages = _round_to_100(raw, protected)

    # If rounding pushed the total off the real bills, pull the difference
    # from the largest flexible bucket.
    if fixed_euros > 0:
        needed_pct = int(round(min(80, fixed_euros / income * 100)))
        if percentages['fixed_expenses'] + 1 < needed_pct:
            gap = needed_pct - percentages['fixed_expenses']
            donor = max(
                (bucket for bucket in BUCKETS if bucket != 'fixed_expenses'),
                key=lambda bucket: percentages[bucket],
            )
            move = min(gap, max(percentages[donor] - 2, 0))
            percentages['fixed_expenses'] += move
            percentages[donor] -= move

    amounts = {bucket: round(income * percentages[bucket] / 100, 2) for bucket in BUCKETS}
    # Penny drift lands on fixed expenses, where the bills already live.
    drift = round(income - sum(amounts.values()), 2)
    amounts['fixed_expenses'] = round(amounts['fixed_expenses'] + drift, 2)

    focus_order = sorted(
        ('security', 'personal_investment', 'travel', 'big_goals'),
        key=lambda bucket: percentages[bucket],
        reverse=True,
    )
    main = focus_order[0]
    headline = (
        f'Sua maior caixinha de sonhos é {LABELS[main]} ({percentages[main]}% · {_money(amounts[main])}).'
    )

    if not excerpt:
        explanations.insert(
            0,
            'Não encontrei um texto de preferências. Usei a renda, os gastos e as prioridades marcadas. '
            'Escreva como quer organizar que a distribuição muda.'
        )
    else:
        explanations.insert(0, f'Li o que você escreveu: “{excerpt[:240]}”.')

    if data.get('urgent_debt'):
        alerts.append('Há uma dívida urgente. A segurança e a quitação ficam na frente dos desejos.')
    fixed_pct = percentages['fixed_expenses']
    if fixed_pct >= 65:
        alerts.append(
            f'Gastos fixos em {fixed_pct}% da renda. Isso aperta reserva, viagens e outros sonhos.'
        )
    emergency_amount = _to_float(data.get('emergency_fund_amount'))
    essential = listed if listed > 0 else amounts['fixed_expenses']
    emergency_goal = max(essential, income * 0.3) * 6
    if (data.get('emergency_fund_status') or 'none') in {'none', 'less_1_month', ''} and emergency_amount < essential:
        alerts.append(
            f'A reserva ainda não cobre um mês de gastos. A meta sugerida é {_money(emergency_goal)} (6 meses).'
        )
    if (data.get('income_type') or '') == 'variavel':
        alerts.append('Como a renda é variável, a fatia de segurança ficou um pouco maior.')
    if data.get('shares_expenses'):
        explanations.append('Você divide as contas da casa, então o peso dos gastos fixos diminuiu um pouco.')
    dependents = int(_to_float(data.get('dependents')))
    if dependents:
        explanations.append(
            f'{dependents} pessoa(s) dependem de você, então o essencial da casa pesou mais.'
        )

    selected = []
    for bucket in focus_order:
        if percentages[bucket] <= 0:
            continue
        selected.append(f'{EMOJIS[bucket]} {LABELS[bucket]}')
    if percentages['fixed_expenses'] > 0:
        selected.append(f'{EMOJIS["fixed_expenses"]} {LABELS["fixed_expenses"]}')

    boxes = []
    progress = 0
    if emergency_goal:
        progress = int(min(100, round(emergency_amount / emergency_goal * 100)))
    boxes.append({
        'emoji': '🛡️',
        'name': 'Reserva de emergência',
        'monthly': amounts['security'],
        'detail': f'Meta {_money(emergency_goal)} · já tem {_money(emergency_amount)}',
        'progress': progress,
    })
    if expenses:
        names = ', '.join(item['name'] for item in expenses[:6])
        boxes.append({
            'emoji': '🏠',
            'name': 'Contas do mês',
            'monthly': amounts['fixed_expenses'],
            'detail': names,
            'progress': int(min(100, round(listed / amounts['fixed_expenses'] * 100))) if amounts['fixed_expenses'] else 0,
        })
    if lifestyle > 0 or percentages['personal_investment'] >= 8:
        boxes.append({
            'emoji': '🌱',
            'name': 'Vida e investimento pessoal',
            'monthly': amounts['personal_investment'],
            'detail': f'Sem culpa: {_money(lifestyle)}' if lifestyle else 'Cursos, saúde, lazer e desenvolvimento',
            'progress': 0,
        })
    for goal in goals:
        goal_progress = int(min(100, round(goal['current'] / goal['amount'] * 100))) if goal['amount'] else 0
        boxes.append({
            'emoji': EMOJIS.get(goal['bucket'], '🎯'),
            'name': goal['name'],
            'monthly': round(min(goal['monthly'], amounts[goal['bucket']]), 2),
            'detail': (
                f'Meta {_money(goal["amount"])} em {goal["months"]} meses · '
                f'falta {_money(max(goal["amount"] - goal["current"], 0))}'
            ),
            'progress': goal_progress,
        })
    if not any(box['name'].lower().startswith('viag') for box in boxes) and percentages['travel'] >= 8:
        boxes.append({
            'emoji': '✈️',
            'name': 'Caixinha de viagens',
            'monthly': amounts['travel'],
            'detail': 'Separada do dinheiro do mês',
            'progress': 0,
        })
    if percentages['big_goals'] >= 8 and not any(box['emoji'] == '🏡' and box['name'] != 'Contas do mês' for box in boxes):
        boxes.append({
            'emoji': '🏡',
            'name': 'Grandes objetivos',
            'monthly': amounts['big_goals'],
            'detail': 'Casa, carro, negócio ou outro plano grande',
            'progress': 0,
        })

    checklist = []
    if expenses:
        checklist.append(
            'Pagar gastos fixos (' + _money(amounts['fixed_expenses']) + '): '
            + ', '.join(item['name'] for item in expenses[:5])
        )
    else:
        checklist.append('Pagar gastos fixos (' + _money(amounts['fixed_expenses']) + ')')
    for bucket in ('security', 'personal_investment', 'travel', 'big_goals'):
        if amounts[bucket] > 0:
            checklist.append(f'Separar {_money(amounts[bucket])} para {LABELS[bucket].lower()}')
    for goal in goals:
        checklist.append(f'Guardar para {goal["name"]} ({_money(goal["monthly"])} sugeridos)')
    checklist.append('Conferir se alguma caixinha passou do combinado')
    checklist.append('Atualizar o saldo das metas')

    if not explanations:
        explanations.append('Distribuição montada com a renda e o essencial do mês.')

    return {
        'monthly_income': round(income, 2),
        'total_income': round(income, 2),
        'security_percentage': percentages['security'],
        'fixed_expenses_percentage': percentages['fixed_expenses'],
        'personal_investment_percentage': percentages['personal_investment'],
        'travel_percentage': percentages['travel'],
        'big_goals_percentage': percentages['big_goals'],
        'security_amount': amounts['security'],
        'fixed_expenses_amount': amounts['fixed_expenses'],
        'personal_investment_amount': amounts['personal_investment'],
        'travel_amount': amounts['travel'],
        'big_goals_amount': amounts['big_goals'],
        'headline': headline,
        'read_excerpt': excerpt[:240],
        'explanations': explanations[:8],
        'alerts': alerts[:5],
        'signals': signals[:10],
        'boxes': boxes[:8],
        'checklist': checklist,
        'selected_priorities': selected,
        'priorities_count': len(selected),
        'ai_summary': None,
        'main_priority': LABELS[main],
        'goals': goals,
        'expenses': expenses,
    }
