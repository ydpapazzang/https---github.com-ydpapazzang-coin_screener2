from django.contrib import admin
from .models import Strategy, Condition, VisitLog

class ConditionInline(admin.TabularInline):
    model = Condition
    extra = 1

@admin.register(Strategy)
class StrategyAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    inlines = [ConditionInline]

@admin.register(Condition)
class ConditionAdmin(admin.ModelAdmin):
    list_display = ('strategy', 'timeframe', 'offset', 'left_indicator', 'operator', 'right_indicator')


@admin.register(VisitLog)
class VisitLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'ip', 'path', 'status_code')
    list_filter = ('status_code',)
    search_fields = ('ip', 'path', 'user_agent')
    date_hierarchy = 'created_at'
