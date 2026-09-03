from load_agent.client import AgentClientError
from load_agent.main import _claim_or_none


def test_claim_configuration_error_keeps_agent_process_alive(caplog):
    class Client:
        def claim(self):
            raise AgentClientError("场景环境配置暂不可执行")

    assert _claim_or_none(Client()) is None
    assert "领取压测分片失败" in caplog.text
