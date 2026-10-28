import json
import unittest

from app import Category, FinancialPlanning, Transaction, User, app, db


class PersonalizedFlowTests(unittest.TestCase):
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
            categories_configured=False,
        )
        db.session.add(self.user)
        db.session.commit()
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.user.id)
            session['_fresh'] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _post_plan(self, name, notes, dreams):
        return self.client.post('/planning', data={
            'monthly_income': '3000',
            'income_type': 'fixa',
            'other_income': '0',
            'dependents': '0',
            'emergency_fund_status': 'none',
            'emergency_fund_amount': '0',
            'lifestyle_budget': '0',
            'dreams': dreams,
            'organization_notes': notes,
            'plan_name': name,
            'plan_date': '2026-10-27',
            'save_as_favorite': 'on',
            'expense_name[]': 'Aluguel',
            'expense_amount[]': '1200',
            'expense_category[]': 'moradia',
        }, follow_redirects=True)

    def test_written_preferences_are_stored_as_different_plans(self):
        first = self._post_plan(
            'Plano viagem',
            'Quero priorizar a viagem para Paris. Guarde 25% para viagens e só 10% para grandes objetivos.',
            'Conhecer Paris',
        )
        self.assertEqual(first.status_code, 200)
        second = self._post_plan(
            'Plano casa',
            'Não quero viajar. Foco total em comprar a casa e na reserva de emergência. 30% para a casa e 20% para segurança. Quase nada para viagens.',
            'Comprar minha casa',
        )
        self.assertEqual(second.status_code, 200)

        plans = {plan.name: plan for plan in FinancialPlanning.query.filter_by(user_id=self.user.id).all()}
        self.assertEqual(set(plans), {'Plano viagem', 'Plano casa'})
        self.assertGreaterEqual(plans['Plano viagem'].travel_percentage, 20)
        self.assertLessEqual(plans['Plano casa'].travel_percentage, 8)
        self.assertGreater(plans['Plano casa'].big_goals_percentage, plans['Plano viagem'].big_goals_percentage)
        stored = json.loads(plans['Plano viagem'].plan_result)
        self.assertIn('viag', ' '.join(stored['explanations']).lower())
        self.assertIn('Aluguel', ' '.join(stored['checklist']))

    def test_chat_confirms_before_saving_natural_language(self):
        draft = self.client.post('/api/chat', json={'message': 'gastei 45 no mercado'})
        self.assertEqual(draft.status_code, 200)
        body = draft.get_json()
        self.assertIn('mercado', body['response'].lower())
        self.assertTrue(any(item['message'] == 'sim' for item in body['suggestions']))
        self.assertEqual(Transaction.query.filter_by(user_id=self.user.id).count(), 0)

        saved = self.client.post('/api/chat', json={'message': 'sim'})
        saved_body = saved.get_json()
        self.assertIn('Mercado', saved_body['response'])
        transaction = Transaction.query.filter_by(user_id=self.user.id).one()
        self.assertEqual(transaction.type, 'expense')
        self.assertEqual(transaction.amount, 45)
        category = db.session.get(Category, transaction.category_id)
        self.assertEqual(category.name, 'Mercado')
        self.assertEqual(saved_body['balances'][0]['balance'], -45)

        undone = self.client.post('/api/chat', json={'message': 'desfazer'})
        self.assertEqual(undone.status_code, 200)
        self.assertEqual(Transaction.query.filter_by(user_id=self.user.id).count(), 0)


if __name__ == '__main__':
    unittest.main()
