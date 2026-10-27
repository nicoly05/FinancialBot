import unittest

from planning_engine import compute_financial_plan


BASE = {
    'monthly_income': 3000,
    'other_income': 0,
    'income_type': 'fixa',
    'dependents': 0,
    'shares_expenses': False,
    'has_debts': False,
    'emergency_fund_status': 'none',
    'emergency_fund_amount': 0,
    'expenses': [{'name': 'Aluguel', 'amount': 1200, 'category': 'moradia'}],
}


def _sum_pct(plan):
    return (
        plan['security_percentage']
        + plan['fixed_expenses_percentage']
        + plan['personal_investment_percentage']
        + plan['travel_percentage']
        + plan['big_goals_percentage']
    )


class PlanningEngineTests(unittest.TestCase):
    def test_written_percentages_change_the_plan(self):
        travel = compute_financial_plan({
            **BASE,
            'organization_notes': (
                'Quero priorizar a viagem para Paris. Guarde 25% para viagens '
                'e só 10% para grandes objetivos.'
            ),
            'dreams': 'Conhecer Paris',
        })
        house = compute_financial_plan({
            **BASE,
            'organization_notes': (
                'Não quero viajar. Foco total em comprar a casa e na reserva de emergência. '
                '30% para a casa e 20% para segurança. Quase nada para viagens.'
            ),
            'dreams': 'Comprar minha casa',
        })

        self.assertEqual(_sum_pct(travel), 100)
        self.assertEqual(_sum_pct(house), 100)
        self.assertGreaterEqual(travel['travel_percentage'], 20)
        self.assertLessEqual(house['travel_percentage'], 8)
        self.assertGreaterEqual(house['big_goals_percentage'], 25)
        self.assertGreaterEqual(house['security_percentage'], 15)
        self.assertNotEqual(travel['travel_percentage'], house['travel_percentage'])
        self.assertNotEqual(travel['big_goals_percentage'], house['big_goals_percentage'])
        self.assertNotEqual(travel['headline'], house['headline'])
        travel_text = ' '.join(travel['explanations']).lower()
        house_text = ' '.join(house['explanations']).lower()
        self.assertIn('viag', travel_text)
        self.assertIn('casa', house_text)
        self.assertGreaterEqual(travel['fixed_expenses_percentage'], 35)
        self.assertIn('Aluguel', ' '.join(travel['checklist']))

    def test_dreams_alone_change_the_focus(self):
        travel = compute_financial_plan({
            'monthly_income': 4000,
            'dreams': 'Viajar para o Japão',
        })
        house = compute_financial_plan({
            'monthly_income': 4000,
            'dreams': 'Comprar minha casa própria',
        })
        self.assertGreater(travel['travel_percentage'], house['travel_percentage'])
        self.assertGreater(house['big_goals_percentage'], travel['big_goals_percentage'])
        self.assertEqual(_sum_pct(travel), 100)
        self.assertEqual(_sum_pct(house), 100)

    def test_zero_income_does_not_crash(self):
        plan = compute_financial_plan({'monthly_income': 0, 'organization_notes': '25% para viagens'})
        self.assertEqual(plan['monthly_income'], 0)
        self.assertTrue(plan['explanations'])


if __name__ == '__main__':
    unittest.main()
