# This script enables and configures all APIs to work in the pipeline
# SportLogic integration included

from app.providers import sportlogic_provider, odds_api_io_provider, bzzoiro_provider, sstats_provider, thesportsdb_provider, weather_provider, futrixmetrics_provider, gnews_provider, meteostat_provider

# Initialize SportLogic
SPORTLOGIC_KEY = 'YOUR_SPORTLOGIC_KEY'
sportlogic = sportlogic_provider.SportLogicProvider(SPORTLOGIC_KEY)

# Initialize odds_api_io accounts
ODDS_API_IO_KEY1 = 'YOUR_ODDS_API_IO_KEY1'
ODDS_API_IO_KEY2 = 'YOUR_ODDS_API_IO_KEY2'
odds_api_io1 = odds_api_io_provider.OddsApiIoProvider(ODDS_API_IO_KEY1, bookmakers=['Bet365','Unibet'])
odds_api_io2 = odds_api_io_provider.OddsApiIoProvider(ODDS_API_IO_KEY2, bookmakers=['Betfair Exchange','Sbobet'])

# Initialize Bzzoiro
bzzoiro = bzzoiro_provider.BzzoiroProvider('YOUR_BZZOIRO_KEY')

# Initialize SStats
sstats = sstats_provider.SStatsProvider('YOUR_SSTATS_KEY')

# Initialize TheSportsDB
thesportsdb = thesportsdb_provider.TheSportsDBProvider('YOUR_THESPORTSDB_KEY')

# Initialize Weather APIs
weatherapi = weather_provider.WeatherAPIProvider('YOUR_WEATHERAPI_KEY')
openweathermap = weather_provider.OpenWeatherMapProvider('YOUR_OPENWEATHERMAP_KEY')

# Initialize FutrixMetrics, GNews, Meteostat
futrixmetrics = futrixmetrics_provider.FutrixMetricsProvider('YOUR_FUTRIX_KEY')
gnews = gnews_provider.GNewsProvider('YOUR_GNEWS_KEY')
meteostat = meteostat_provider.MeteostatProvider('YOUR_METEOSTAT_KEY')

# Function to initialize all APIs and check connectivity
def initialize_all_apis():
    # Check SportLogic
    sportlogic.get_fixtures()
    # Check odds_api_io accounts
    odds_api_io1.get_odds(1)
    odds_api_io2.get_odds(1)
    # Check Bzzoiro
    bzzoiro.get_data()
    # Check SStats
    sstats.get_stats()
    # Check TheSportsDB
    thesportsdb.get_data()
    # Check Weather
    weatherapi.get_weather('London')
    openweathermap.get_weather('London')
    # Check FutrixMetrics
    futrixmetrics.get_data()
    # Check GNews
    gnews.get_news('football')
    # Check Meteostat
    meteostat.get_weather('London')
    print('All APIs initialized and working')

if __name__ == '__main__':
    initialize_all_apis()