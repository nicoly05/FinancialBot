# FinancialBot

Um bot financeiro inteligente com dashboard interativo para gestão de finanças pessoais.

## Funcionalidades

- **Sistema de Autenticação**: Criação de conta com perguntas de segurança e recuperação de palavra-passe
- **Dashboard Financeiro**: Gráficos interativos (pizza, linha, barras) com análise de gastos
- **Chat Inteligente**: Configuração de categorias e subcategorias via conversação natural
- **Gestão de Valores**: Adiciona ou remove valores via chat
- **Persistência de Sessão**: Histórico de chat e dados financeiros guardados
- **Interface Moderna**: Design responsivo e minimalista

## Tecnologias

- Python 3.8+
- Flask (Web Framework)
- SQLite (Base de Dados)
- SQLAlchemy (ORM)
- Chart.js (Gráficos)
- Bootstrap 5 (UI)
- Flask-Login (Autenticação)
- bcrypt (Hashing de passwords)

## Instalação

### Pré-requisitos

- Python 3.8 ou superior

### Passos

1. **Clona o repositório**
   ```bash
   cd FinancialBot
   ```

2. **Cria um ambiente virtual**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   # ou
   venv\Scripts\activate  # Windows
   ```

3. **Instala as dependências**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configura as variáveis de ambiente**
   ```bash
   cp .env.example .env
   ```
   
   Edita o ficheiro `.env` e configura:
   - `DATABASE_URL`: URL de conexão à base de dados (default: `sqlite:///financial_bot.db`)
   - `SECRET_KEY`: Chave secreta para sessões Flask (gera uma segura para produção)

5. **Executa a aplicação**
   ```bash
   python3 app.py
   ```

6. **Acede à aplicação**
   Abre o navegador em `http://localhost:5001`

## Utilização

### 1. Criar Conta

- Clica em "Regista-te"
- Preenche o nome de utilizador e palavra-passe
- Escolhe uma pergunta de segurança e resposta
- Faz login com as tuas credenciais

### 2. Configurar Categorias (via Chat)

- Vai à secção "Chat"
- Escreve "oi" para iniciar
- Indica as tuas categorias no formato:
  ```
  1. futuro
  2. viagens
  3. investimento
  ```
- Opcionalmente, adiciona subcategorias selecionando o número da categoria

### 3. Adicionar/Remover Valores

- Após configurar as categorias, usa os comandos:
  - `adicionar 400` - para adicionar um valor
  - `remover 200` - para remover um valor
- O bot pedirá para escolher a categoria (e subcategoria se aplicável)

### 4. Visualizar Dashboard

- Vai à secção "Dashboard"
- Visualiza os gráficos de pizza, linha ou barras
- Vê as estatísticas de gastos por categoria
- Consulta o histórico de transações

## Estrutura do Projeto

```
FinancialBot/
├── app.py                 # Aplicação Flask principal
├── config.py              # Configurações
├── requirements.txt       # Dependências Python
├── .env.example          # Exemplo de variáveis de ambiente
├── templates/            # Templates Jinja2
│   ├── base.html         # Template base
│   ├── login.html        # Página de login
│   ├── register.html     # Página de registo
│   ├── recover.html      # Recuperação de palavra-passe
│   ├── dashboard.html    # Dashboard financeiro
│   └── chat.html         # Interface de chat
└── README.md             # Este ficheiro
```

## Segurança

- Palavras-passe hashadas com bcrypt
- Perguntas de segurança para recuperação de conta
- Sessões protegidas com Flask-Login
- Proteção CSRF em formulários
- Base de dados SQLite local (sem necessidade de servidor externo)

## Desenvolvimento

Para desenvolvimento, a aplicação corre em modo debug por defeito. Para produção:

- Define `DEBUG=False` no config
- Usa uma `SECRET_KEY` segura
- Considera usar PostgreSQL ou MySQL para produção em vez de SQLite
- Usa HTTPS em produção

## Licença

Este projeto é para fins educacionais.
