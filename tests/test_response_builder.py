from hermes.agent.response_builder import ResponseBuilder


class TestResponseBuilder:
    def test_dedupe_final_output_when_streamed(self):
        builder = ResponseBuilder()
        builder.add_delta("Merhaba, test basarili.")
        builder.add_final_output("Merhaba, test basarili.")
        assert builder.build() == "Merhaba, test basarili."

    def test_dedupe_final_output_prefix(self):
        builder = ResponseBuilder()
        builder.add_delta("Merhaba")
        builder.add_final_output("Merhaba, tam metin.")
        assert builder.build() == "Merhaba"

    def test_keeps_extras_separate(self):
        builder = ResponseBuilder()
        builder.add_delta("Yanit")
        builder.add_extra("Onay gerekli")
        assert "Yanit" in builder.build()
        assert "Onay gerekli" in builder.build()

    def test_no_duplicate_extras(self):
        builder = ResponseBuilder()
        builder.add_extra("OK")
        builder.add_extra("OK")
        assert builder.build() == "OK"
