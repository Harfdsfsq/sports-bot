from __future__ import annotations
import requests

class SportLogicProvider:
    BASE_URL = 'https://api.sportlogic.io/v1'
    MAX_DAILY = 500

    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_fixtures(self, date: str | None = None):
        url = f'{self.BASE_URL}/football/fixtures'
        headers = {'Authorization': f'Bearer {self.api_key}'}
        params = {'date': date} if date else {}
        response = requests.get(url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        return response.json()

    def get_odds(self, fixture_id: int):
        url = f'{self.BASE_URL}/football/odds/{fixture_id}'
        headers = {'Authorization': f'Bearer {self.api_key}'}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        return response.json()

    def get_results(self, fixture_id: int):
        url = f'{self.BASE_URL}/football/results/{fixture_id}'
        headers = {'Authorization': f'Bearer {self.api_key}'}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        return response.json()