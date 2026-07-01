import unittest
from sqlalchemy import inspect

from app import app, db, init_database, User, Category


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
            subcategories='[]'
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


if __name__ == '__main__':
    unittest.main()
