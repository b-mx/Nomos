"""Run directly: uv run pytest tests/sources/test_purl.py -v"""

from tools.sources._lib import ecosystem_to_purl


def test_pypi_maps_directly() -> None:
    assert ecosystem_to_purl("PyPI", "pytorch") == "pkg:pypi/pytorch"


def test_npm_maps_directly() -> None:
    assert ecosystem_to_purl("npm", "left-pad") == "pkg:npm/left-pad"


def test_maven_splits_group_and_artifact() -> None:
    assert ecosystem_to_purl("Maven", "org.apache.logging.log4j:log4j-core") == (
        "pkg:maven/org.apache.logging.log4j/log4j-core"
    )


def test_maven_without_colon_returns_none() -> None:
    assert ecosystem_to_purl("Maven", "log4j-core") is None


def test_unknown_ecosystem_returns_none() -> None:
    assert ecosystem_to_purl("SomeNewEcosystem", "whatever") is None


def test_cargo_and_golang_and_others() -> None:
    assert ecosystem_to_purl("crates.io", "serde") == "pkg:cargo/serde"
    assert ecosystem_to_purl("Go", "github.com/gorilla/mux") == "pkg:golang/github.com/gorilla/mux"
    assert ecosystem_to_purl("NuGet", "Newtonsoft.Json") == "pkg:nuget/Newtonsoft.Json"
    assert ecosystem_to_purl("RubyGems", "rails") == "pkg:gem/rails"
    assert ecosystem_to_purl("Packagist", "monolog/monolog") == "pkg:composer/monolog/monolog"
    assert ecosystem_to_purl("Hex", "phoenix") == "pkg:hex/phoenix"
    assert ecosystem_to_purl("Pub", "http") == "pkg:pub/http"
