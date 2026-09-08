import unittest
from sqlalchemy import inspect

from app import app, db, init_database, User, Category, FinancialPlanning


class CategoryManagementTests(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:'
        )
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_database_tables_are_created_on_app_start(self):
        db.drop_all()
        init_database()

        with app.app_context():
            tables = inspect(db.engine).get_table_names()

        self.assertIn('user', tables)
        self.assertIn('category', tables)

    def test_dashboard_renders_explicit_edit_and_delete_actions_for_categories(self):
        user = User(
            username='testuser',
            password=b'hashed',
            security_question='question',
            security_answer=b'answer',
            categories_configured=False
        )
        db.session.add(user)
        db.session.commit()

        category = Category(
            user_id=user.id,
            name='Alimentação',
            emoji='🍽️',
            color='#ff0000',
            subcategories=[]
        )
        db.session.add(category)
        db.session.commit()

        with self.client.session_transaction() as session:
            session['_user_id'] = str(user.id)
            session['_fresh'] = True

        response = self.client.get('/dashboard')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="categoryList"', html)
        self.assertNotIn('id="categoryEditorList"', html)
        self.assertIn('data-action="edit-category"', html)
        self.assertIn('data-action="delete-category"', html)
        self.assertIn('data-action="add-category"', html)

    def test_planning_page_shows_required_sections(self):
        user = User(
            username='planner2',
            password=b'hashed',
            security_question='question',
            security_answer=b'answer',
            categories_configured=False
        )
        db.session.add(user)
        db.session.commit()

        with self.client.session_transaction() as session:
            session['_user_id'] = str(user.id)
            session['_fresh'] = True

        response = self.client.get('/planning')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Meu planejamento', html)
        self.assertIn('Faça um novo planejamento', html)
        self.assertIn('Histórico de planejamentos', html)

    def test_multiple_planning_sessions_are_kept_for_same_user(self):
        user = User(
            username='planner',
            password=b'hashed',
            security_question='question',
            security_answer=b'answer',
            categories_configured=False
        )
        db.session.add(user)
        db.session.commit()

        with self.client.session_transaction() as session:
            session['_user_id'] = str(user.id)
            session['_fresh'] = True

        first_response = self.client.post('/planning', data={
            'monthly_income': '3000',
            'income_type': 'fixa',
            'other_income': '500',
            'dependents': '1',
            'shares_expenses': 'on',
            'has_debts': 'on',
            'total_debt': '1000',
            'monthly_debt_payment': '200',
            'urgent_debt': 'on',
            'emergency_fund_status': 'less_1_month',
            'emergency_fund_amount': '1000',
            'has_investments': 'on',
            'investment_amount': '2000',
            'monthly_investment': '150',
            'has_retirement': 'on',
            'current_age': '30',
            'retirement_age': '65',
            'lifestyle_budget': '300',
            'priorities': '[]',
            'dreams': 'Viajar',
            'plan_name': 'Primeiro planejamento',
            'plan_observation': 'Meta inicial',
            'plan_date': '2026-09-01',
            'save_as_favorite': 'on'
        }, follow_redirects=True)
        self.assertEqual(first_response.status_code, 200)

        second_response = self.client.post('/planning', data={
            'monthly_income': '4200',
            'income_type': 'variavel',
            'other_income': '700',
            'dependents': '2',
            'shares_expenses': 'off',
            'has_debts': 'off',
            'emergency_fund_status': '1_3_months',
            'emergency_fund_amount': '2000',
            'has_investments': 'off',
            'lifestyle_budget': '500',
            'priorities': '[]',
            'dreams': 'Comprar casa',
            'plan_name': 'Segundo planejamento',
            'plan_observation': 'Replanejamento',
            'plan_date': '2026-09-07',
            'save_as_favorite': 'off'
        }, follow_redirects=True)
        self.assertEqual(second_response.status_code, 200)

        saved_plans = FinancialPlanning.query.filter_by(user_id=user.id).all()
        self.assertEqual(len(saved_plans), 2)
        self.assertEqual(sorted(plan.name for plan in saved_plans), ['Primeiro planejamento', 'Segundo planejamento'])


if __name__ == '__main__':
    unittest.main()
