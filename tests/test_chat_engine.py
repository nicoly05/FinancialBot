import unittest

from chat_engine import interpret_message


class ChatEngineTests(unittest.TestCase):
    def test_expense_in_natural_language(self):
        result = interpret_message('gastei 45 no mercado')
        self.assertEqual(result['kind'], 'transactions')
        tx = result['transactions'][0]
        self.assertEqual(tx['type'], 'expense')
        self.assertEqual(tx['amount'], 45)
        self.assertEqual(tx['category'], 'mercado')

    def test_income_with_accent(self):
        result = interpret_message('recebi 2500 de salário')
        tx = result['transactions'][0]
        self.assertEqual(tx['type'], 'income')
        self.assertEqual(tx['amount'], 2500)
        self.assertEqual(tx['category'], 'salário')

    def test_two_entries_in_one_sentence(self):
        result = interpret_message('recebi 2500 de salário e gastei 80 no mercado')
        self.assertEqual(len(result['transactions']), 2)
        self.assertEqual(result['transactions'][0]['type'], 'income')
        self.assertEqual(result['transactions'][1]['type'], 'expense')
        self.assertEqual(result['transactions'][1]['category'], 'mercado')

    def test_legacy_commands_and_confirmation(self):
        added = interpret_message('adicionar 400')
        self.assertEqual(added['transactions'][0]['type'], 'income')
        self.assertEqual(added['transactions'][0]['amount'], 400)
        self.assertIsNone(added['transactions'][0]['category'])
        self.assertEqual(interpret_message('sim')['kind'], 'confirm')
        self.assertEqual(interpret_message('desfazer')['kind'], 'undo')
        self.assertEqual(interpret_message('oi')['kind'], 'help')

    def test_inherited_type_on_second_clause(self):
        result = interpret_message('adicionar 50 em alimentação e 20 em lazer')
        self.assertEqual(len(result['transactions']), 2)
        self.assertEqual(result['transactions'][0]['category'], 'alimentação')
        self.assertEqual(result['transactions'][1]['type'], 'income')
        self.assertEqual(result['transactions'][1]['category'], 'lazer')
        self.assertEqual(result['transactions'][1]['amount'], 20)


if __name__ == '__main__':
    unittest.main()
