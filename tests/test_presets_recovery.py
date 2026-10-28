import unittest

from app import User, UserPreset, app, db
from config import normalize_database_url


class DatabaseUrlTests(unittest.TestCase):
    def test_sqlite_fallback_and_supabase_postgres(self):
        self.assertTrue(normalize_database_url('').startswith('sqlite:///'))
        self.assertTrue(normalize_database_url(None).startswith('sqlite:///'))
        postgres = normalize_database_url('postgres://user:senha@db.abc.supabase.co:5432/postgres')
        self.assertTrue(postgres.startswith('postgresql+psycopg://'))
        self.assertIn('sslmode=require', postgres)
        already = normalize_database_url('postgresql+psycopg://user:senha@localhost/db')
        self.assertTrue(already.startswith('postgresql+psycopg://'))


class PresetAndRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()
        self.user = User(
            username='nicoly',
            password=b'hashed',
            security_question='question',
            security_answer=b'answer',
            email='nicoly@example.com',
            categories_configured=False,
        )
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user.id)
            session['_fresh'] = True

    def test_preset_asks_before_using_a_fixed_amount(self):
        self._login()
        first = self.client.post('/api/chat', json={'message': '__preset__:cafe'}).get_json()
        self.assertIn('valor habitual', first['response'].lower())
        self.assertNotIn('18', first['response'])
        self.assertIsNone(first['saved'])

        second = self.client.post('/api/chat', json={'message': '2'}).get_json()
        self.assertIn('2.00', second['response'])
        self.assertTrue(any(item['message'] == 'sim' for item in second['suggestions']))
        saved = UserPreset.query.filter_by(user_id=self.user.id, key='cafe').one()
        self.assertEqual(saved.amount, 2)

        self.client.post('/api/chat', json={'message': 'sim'})
        again = self.client.post('/api/chat', json={'message': '__preset__:cafe'}).get_json()
        self.assertIn('2.00', again['response'])
        self.assertNotIn('18', again['response'])

        edited = self.client.post('/api/chat', json={'message': 'alterar café para 3'}).get_json()
        self.assertIn('3.00', edited['response'])
        self.assertEqual(UserPreset.query.filter_by(user_id=self.user.id, key='cafe').one().amount, 3)

    def test_profile_edits_habitual_amount_and_requires_email(self):
        self._login()
        missing = self.client.post('/profile', data={'email': ''}, follow_redirects=True)
        self.assertIn('obrigatório', missing.get_data(as_text=True).lower())

        saved = self.client.post('/profile', data={
            'email': 'nicoly@example.com',
            'full_name': 'Nicoly',
            'phone': '+351900000000',
            'city': 'Lisboa',
            'currency': 'EUR',
            'occupation': 'Designer',
            'preset_cafe': '2.50',
        }, follow_redirects=True)
        self.assertEqual(saved.status_code, 200)
        db.session.refresh(self.user)
        self.assertEqual(self.user.full_name, 'Nicoly')
        self.assertEqual(self.user.city, 'Lisboa')
        self.assertEqual(UserPreset.query.filter_by(user_id=self.user.id, key='cafe').one().amount, 2.5)
        page = self.client.get('/chat')
        self.assertNotIn('Gastei 18', page.get_data(as_text=True))
        self.assertIn('__preset__:cafe', page.get_data(as_text=True))

    def test_password_reset_code_expires_path_and_rate_limit(self):
        sent = self.client.post('/recover', data={'action': 'send', 'email': 'nicoly@example.com'})
        self.assertEqual(sent.status_code, 200)
        code = self.app.config.get('LAST_RESET_CODE')
        self.assertRegex(code, r'^\d{6}$')

        wrong = self.client.post('/recover', data={
            'action': 'verify', 'email': 'nicoly@example.com', 'code': '000000',
        })
        self.assertIn('inválido', wrong.get_data(as_text=True).lower())

        verified = self.client.post('/recover', data={
            'action': 'verify', 'email': 'nicoly@example.com', 'code': code,
        })
        self.assertIn('Nova senha', verified.get_data(as_text=True))
        reset = self.client.post('/recover', data={
            'action': 'reset', 'new_password': 'nova123', 'confirm_password': 'nova123',
        }, follow_redirects=True)
        self.assertIn('nova senha', reset.get_data(as_text=True).lower())

        import bcrypt
        db.session.refresh(self.user)
        self.assertTrue(bcrypt.checkpw(b'nova123', self.user.password))

        for _ in range(3):
            self.client.post('/recover', data={'action': 'send', 'email': 'outro@example.com'})
        blocked = self.client.post('/recover', data={'action': 'send', 'email': 'outro@example.com'})
        self.assertIn('Espere alguns minutos', blocked.get_data(as_text=True))

    def test_planning_result_is_not_rendered_twice(self):
        self._login()
        html = self.client.get('/planning').get_data(as_text=True)
        self.assertNotIn('id="livePlanResult"', html)
        self.assertEqual(html.count('id="planningResult"'), 1)
