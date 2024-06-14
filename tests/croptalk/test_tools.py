from dotenv import load_dotenv
from croptalk.tools import get_sob_endpoint
load_dotenv("secrets/.env.secret")


def test_get_sob_endpoint():

    response = get_sob_endpoint(state_code="53", commodity_code="0089", insurance_plan_code="90")

    assert response.status_code == 200
