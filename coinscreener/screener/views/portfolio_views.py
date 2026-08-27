import math

from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from ..models import DailyRecommendation, PaperPosition
from ..ownership import get_owner_key
from ..position_sizing import calculate_position_size


def _positive_number(raw):
    try:
        value = float(str(raw).replace(',', ''))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def portfolio_list(request):
    owner_key = get_owner_key(request)
    selected = request.GET.get('trade_type', '')
    positions = PaperPosition.objects.filter(owner_key=owner_key)
    if selected in {'danta', 'swing'}:
        positions = positions.filter(trade_type=selected)
    else:
        selected = ''

    items = list(positions)
    invested = sum(item.invested_amount for item in items)
    open_profit = sum(item.profit_amount or 0 for item in items if item.status == 'open')
    realized_profit = sum(item.profit_amount or 0 for item in items if item.status == 'closed')
    curve = []
    cumulative = 0
    for item in sorted(
        (item for item in items if item.status == 'closed'),
        key=lambda value: value.exit_at or value.updated_at,
    ):
        cumulative += item.profit_amount or 0
        curve.append({
            'label': timezone.localtime(item.exit_at).strftime('%m.%d') if item.exit_at else '-',
            'value': round(cumulative, 2),
        })

    return render(request, 'screener/portfolio.html', {
        'positions': items,
        'selected_trade_type': selected,
        'invested': invested,
        'open_profit': open_profit,
        'realized_profit': realized_profit,
        'curve_json': curve,
    })


@require_http_methods(['GET', 'POST'])
def portfolio_add(request, recommendation_id):
    recommendation = get_object_or_404(
        DailyRecommendation, id=recommendation_id
    )
    if recommendation.coin_ticker == 'SKIP':
        return redirect('portfolio_list')
    owner_key = get_owner_key(request)
    existing = PaperPosition.objects.filter(
        owner_key=owner_key, recommendation=recommendation
    ).first()
    if existing:
        return redirect('portfolio_list')

    error = ''
    if request.method == 'POST':
        entry_price = _positive_number(request.POST.get('entry_price'))
        invested_amount = _positive_number(request.POST.get('invested_amount'))
        if entry_price is None or invested_amount is None:
            error = '체결가와 투입금액에 0보다 큰 숫자를 입력하세요.'
        elif not recommendation.stop_loss < entry_price < recommendation.target_price:
            error = '체결가는 손절가보다 높고 목표가보다 낮아야 합니다.'
        else:
            try:
                PaperPosition.objects.create(
                    owner_key=owner_key,
                    recommendation=recommendation,
                    trade_type=recommendation.trade_type,
                    coin_ticker=recommendation.coin_ticker,
                    coin_name=recommendation.coin_name,
                    entry_price=entry_price,
                    invested_amount=invested_amount,
                    target_price=recommendation.target_price,
                    stop_loss=recommendation.stop_loss,
                    current_price=entry_price,
                    highest_price=entry_price,
                    lowest_price=entry_price,
                )
                return redirect('portfolio_list')
            except IntegrityError:
                return redirect('portfolio_list')

    return render(request, 'screener/portfolio_add.html', {
        'recommendation': recommendation,
        'error': error,
    })


@require_POST
def portfolio_close(request, position_id):
    position = get_object_or_404(
        PaperPosition,
        id=position_id,
        owner_key=get_owner_key(request),
        status='open',
    )
    exit_price = _positive_number(request.POST.get('exit_price'))
    if exit_price is not None:
        position.current_price = exit_price
        position.exit_price = exit_price
        position.exit_reason = 'manual'
        position.exit_at = timezone.now()
        position.status = 'closed'
        position.save()
    return redirect('portfolio_list')


def position_size_calculator(request):
    fields = {
        'total_assets': request.GET.get('total_assets', '10000000').strip(),
        'risk_pct': request.GET.get('risk_pct', '0.5').strip(),
        'entry_price': request.GET.get('entry_price', '').strip(),
        'stop_loss': request.GET.get('stop_loss', '').strip(),
    }
    result = None
    error = ''
    if request.GET.get('calculate'):
        try:
            result = calculate_position_size(**fields)
        except ValueError as exc:
            error = str(exc)
    return render(request, 'screener/position_size_calculator.html', {
        'fields': fields,
        'result': result,
        'error': error,
    })

