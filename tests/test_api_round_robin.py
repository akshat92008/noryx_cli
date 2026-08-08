from nexus.api import RoundRobinKeyPool, _response_usage


def test_round_robin_key_pool():
    pool = RoundRobinKeyPool(["key1", "key2"])
    
    assert pool.get_next_key() == "key1"
    assert pool.get_next_key() == "key2"
    assert pool.get_next_key() == "key1"
    
    pool.mark_cooldown("key2", duration=60.0)
    assert pool.get_next_key() == "key1"
    assert pool.get_next_key() == "key1"

def test_round_robin_key_pool_empty():
    pool = RoundRobinKeyPool([])
    assert pool.get_next_key() is None

def test_response_usage_dict():
    class Dummy:
        pass
    obj = Dummy()
    obj.usage = {"prompt_tokens": 10, "completion_tokens": 20}
    usage = _response_usage(obj)
    assert usage["total_tokens"] == 30

def test_response_usage_obj():
    class Dummy:
        pass
    obj = Dummy()
    usage_obj = Dummy()
    usage_obj.prompt_tokens = 5
    usage_obj.completion_tokens = 15
    obj.usage = usage_obj
    
    usage = _response_usage(obj)
    assert usage["total_tokens"] == 20
