from django.db.models import Q


def public_samples(model):
    """NULL/빈 owner_key는 공용 예시이며 개인 전략은 절대 포함하지 않는다."""
    return model.objects.filter(Q(owner_key__isnull=True) | Q(owner_key=''))


def prune_public_samples(model, keep_name='월봉이'):
    targets = public_samples(model).exclude(name=keep_name)
    target_ids = list(targets.values_list('id', flat=True))
    deleted_count, _details = targets.delete()
    return target_ids, deleted_count

