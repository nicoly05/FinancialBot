"""Envio do código de recuperação. Sem SMTP, o código vai para o console."""

import smtplib
from email.message import EmailMessage

from flask import current_app


def smtp_configured():
    return bool((current_app.config.get('SMTP_HOST') or '').strip())


def send_password_reset(to_email, username, code):
    if not smtp_configured():
        line = (
            f'[FinancialBot] Código de recuperação para {to_email} ({username}): '
            f'{code} (expira em 15 minutos)'
        )
        print(line, flush=True)
        current_app.logger.warning(line)
        if current_app.config.get('TESTING'):
            current_app.config['LAST_RESET_CODE'] = code
        return 'console'

    message = EmailMessage()
    message['Subject'] = 'Código para redefinir a senha — FinancialBot'
    message['From'] = current_app.config.get('SMTP_FROM') or current_app.config.get('SMTP_USER')
    message['To'] = to_email
    message.set_content(
        f'Olá, {username}.\n\n'
        f'Seu código para redefinir a senha é {code}.\n'
        'Ele expira em 15 minutos. Se você não pediu isso, ignore este e-mail.\n'
    )
    host = current_app.config['SMTP_HOST']
    port = int(current_app.config.get('SMTP_PORT') or 587)
    user = current_app.config.get('SMTP_USER') or ''
    password = current_app.config.get('SMTP_PASSWORD') or ''
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if current_app.config.get('SMTP_USE_TLS', True):
            smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(message)
    return 'smtp'
