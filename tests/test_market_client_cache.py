from portfolio.market import MarketDataClient


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeTicker:
    def __init__(self, symbol: str, last_price: float) -> None:
        self.symbol = symbol
        self.fast_info = {"lastPrice": last_price, "previousClose": last_price, "currency": "USD"}


class PriceFeed:
    """Each new Ticker sees the market's current price, like a fresh yfinance lookup."""

    def __init__(self, price: float) -> None:
        self.price = price
        self.created_count = 0

    def new_ticker(self, symbol: str) -> FakeTicker:
        self.created_count += 1
        return FakeTicker(symbol, self.price)


def _client(feed: PriceFeed, clock: FakeClock) -> MarketDataClient:
    return MarketDataClient(ttl_seconds=300, ticker_factory=feed.new_ticker, clock=clock)


def test_quote_reuses_ticker_within_ttl():
    feed, clock = PriceFeed(224.52), FakeClock()
    client = _client(feed, clock)
    client.quote("NVDA")
    clock.now += 299
    feed.price = 181.60

    assert client.quote("NVDA").last_price == 224.52
    assert feed.created_count == 1


def test_quote_refreshes_after_ttl_expires():
    feed, clock = PriceFeed(224.52), FakeClock()
    client = _client(feed, clock)
    client.quote("NVDA")
    clock.now += 300
    feed.price = 181.60

    assert client.quote("NVDA").last_price == 181.60
    assert feed.created_count == 2


def test_cache_expiry_is_per_symbol():
    feed, clock = PriceFeed(224.52), FakeClock()
    client = _client(feed, clock)
    client.quote("NVDA")
    clock.now += 200
    client.quote("VTI")
    clock.now += 150

    client.quote("NVDA")
    client.quote("VTI")

    assert feed.created_count == 3
