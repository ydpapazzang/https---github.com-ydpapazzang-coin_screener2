"""KOSPI/ETF 검색에서 현금관리형 상품을 제외하는 공통 규칙."""

CASH_MANAGEMENT_NAME_KEYWORDS = (
    "머니마켓",
    "머니액티브",
    "CD금리",
    "KOFR",
    "금리투자",
    "금리액티브",
    "초단기채",
    "단기채권",
    "단기통안채",
    "단기금융",
    "파킹",
)


def _normalize_product_name(name):
    """공백·괄호·하이픈 차이를 무시하고 상품명을 비교한다."""
    return "".join(char for char in str(name).upper() if char.isalnum())


_NORMALIZED_CASH_KEYWORDS = tuple(
    _normalize_product_name(keyword)
    for keyword in CASH_MANAGEMENT_NAME_KEYWORDS
)


def is_kospi_cash_management_product(name):
    """머니마켓·금리·초단기채 등 현금관리형 ETF이면 True."""
    normalized_name = _normalize_product_name(name)
    return any(
        keyword in normalized_name
        for keyword in _NORMALIZED_CASH_KEYWORDS
    )


def filter_kospi_products(products):
    """name 키가 있는 종목 목록에서 현금관리형 ETF를 제거한다."""
    return [
        product
        for product in products
        if not is_kospi_cash_management_product(product.get("name", ""))
    ]
