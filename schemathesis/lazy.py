from inspect import signature
from typing import Any, Callable, Dict, Iterable, Optional, Union

import attr
from _pytest.fixtures import FixtureRequest
from pytest_subtests import SubTests

from .constants import DEFAULT_DATA_GENERATION_METHODS, DataGenerationMethod
from .hooks import HookDispatcher, HookScope
from .models import Endpoint
from .schemas import BaseSchema
from .types import Filter, GenericTest, NotSet
from .utils import NOT_SET


@attr.s(slots=True)  # pragma: no mutate
class LazySchema:
    fixture_name: str = attr.ib()  # pragma: no mutate
    method: Optional[Filter] = attr.ib(default=NOT_SET)  # pragma: no mutate
    endpoint: Optional[Filter] = attr.ib(default=NOT_SET)  # pragma: no mutate
    tag: Optional[Filter] = attr.ib(default=NOT_SET)  # pragma: no mutate
    operation_id: Optional[Filter] = attr.ib(default=NOT_SET)  # pragma: no mutate
    hooks: HookDispatcher = attr.ib(factory=lambda: HookDispatcher(scope=HookScope.SCHEMA))  # pragma: no mutate
    validate_schema: bool = attr.ib(default=True)  # pragma: no mutate
    skip_deprecated_endpoints: bool = attr.ib(default=False)  # pragma: no mutate
    data_generation_methods: Iterable[DataGenerationMethod] = attr.ib(default=DEFAULT_DATA_GENERATION_METHODS)

    def parametrize(
        self,
        method: Optional[Filter] = NOT_SET,
        endpoint: Optional[Filter] = NOT_SET,
        tag: Optional[Filter] = NOT_SET,
        operation_id: Optional[Filter] = NOT_SET,
        validate_schema: Union[bool, NotSet] = NOT_SET,
        skip_deprecated_endpoints: Union[bool, NotSet] = NOT_SET,
        data_generation_methods: Union[Iterable[DataGenerationMethod], NotSet] = NOT_SET,
    ) -> Callable:
        if method is NOT_SET:
            method = self.method
        if endpoint is NOT_SET:
            endpoint = self.endpoint
        if tag is NOT_SET:
            tag = self.tag
        if operation_id is NOT_SET:
            operation_id = self.operation_id
        if data_generation_methods is NOT_SET:
            data_generation_methods = self.data_generation_methods

        def wrapper(func: Callable) -> Callable:
            def test(request: FixtureRequest, subtests: SubTests) -> None:
                """The actual test, which is executed by pytest."""
                if hasattr(test, "_schemathesis_hooks"):
                    func._schemathesis_hooks = test._schemathesis_hooks  # type: ignore
                schema = get_schema(
                    request=request,
                    name=self.fixture_name,
                    method=method,
                    endpoint=endpoint,
                    tag=tag,
                    operation_id=operation_id,
                    hooks=self.hooks,
                    test_function=func,
                    validate_schema=validate_schema,
                    skip_deprecated_endpoints=skip_deprecated_endpoints,
                    data_generation_methods=data_generation_methods,
                )
                fixtures = get_fixtures(func, request)
                # Changing the node id is required for better reporting - the method and endpoint will appear there
                node_id = subtests.item._nodeid
                settings = getattr(test, "_hypothesis_internal_use_settings", None)
                tests = list(schema.get_all_tests(func, settings))
                request.session.testscollected += len(tests)
                for _endpoint, data_generation_method, sub_test in tests:
                    subtests.item._nodeid = _get_node_name(node_id, _endpoint, data_generation_method)
                    run_subtest(_endpoint, fixtures, sub_test, subtests)
                subtests.item._nodeid = node_id

            # Needed to prevent a failure when settings are applied to the test function
            test.is_hypothesis_test = True  # type: ignore

            return test

        return wrapper


def _get_node_name(node_id: str, endpoint: Endpoint, data_generation_method: DataGenerationMethod) -> str:
    """Make a test node name. For example: test_api[GET:/users]."""
    return f"{node_id}[{endpoint.method.upper()}:{endpoint.full_path}][{data_generation_method.as_short_name()}]"


def run_subtest(endpoint: Endpoint, fixtures: Dict[str, Any], sub_test: Callable, subtests: SubTests) -> None:
    """Run the given subtest with pytest fixtures."""
    with subtests.test(method=endpoint.method.upper(), path=endpoint.path):
        sub_test(**fixtures)


def get_schema(
    *,
    request: FixtureRequest,
    name: str,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    test_function: GenericTest,
    hooks: HookDispatcher,
    validate_schema: Union[bool, NotSet] = NOT_SET,
    skip_deprecated_endpoints: Union[bool, NotSet] = NOT_SET,
    data_generation_methods: Union[Iterable[DataGenerationMethod], NotSet] = NOT_SET,
) -> BaseSchema:
    """Loads a schema from the fixture."""
    schema = request.getfixturevalue(name)
    if not isinstance(schema, BaseSchema):
        raise ValueError(f"The given schema must be an instance of BaseSchema, got: {type(schema)}")
    return schema.clone(
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        test_function=test_function,
        hooks=hooks,
        validate_schema=validate_schema,
        skip_deprecated_endpoints=skip_deprecated_endpoints,
        data_generation_methods=data_generation_methods,
    )


def get_fixtures(func: Callable, request: FixtureRequest) -> Dict[str, Any]:
    """Load fixtures, needed for the test function."""
    sig = signature(func)
    return {name: request.getfixturevalue(name) for name in sig.parameters if name != "case"}
