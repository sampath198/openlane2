# Copyright 2023 Efabless Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import shlex
import inspect
from enum import Enum
from decimal import Decimal, InvalidOperation
from dataclasses import (
    _MISSING_TYPE,
    MISSING,
    asdict,
    dataclass,
    field,
    fields,
    is_dataclass,
)
from typing import (
    ClassVar,
    Dict,
    List,
    Literal,
    Optional,
    Set,
    Tuple,
    Union,
    Mapping,
    Callable,
    Type,
    Any,
    get_origin,
    get_args,
)
from ..state import DesignFormat, State
from ..common import GenericDict, Path, is_string, zip_first, Number, slugify

# Scalar = Union[Type[str], Type[Decimal], Type[Path], Type[bool]]
# VType = Union[Scalar, List[Scalar]]


class MissingRequiredVariable(ValueError):
    def __init__(self, variable: "Variable", pdk: bool = False) -> None:
        self.variable = variable
        if self.variable.pdk:
            super().__init__(
                f"Required PDK variable '{self.variable.name}' did not get a specified value. This PDK may be incompatible with your flow."
            )
        else:
            super().__init__(
                f"Required variable '{self.variable.name}' did not get a specified value."
            )


class Orientation(str, Enum):
    N = "N"
    FN = "FN"
    W = "W"
    FW = "FW"
    S = "S"
    FS = "FS"
    E = "E"
    FE = "FE"
    # OpenAccess
    R0 = "N"
    MY = "FN"
    R90 = "W"
    MXR90 = "FW"
    R180 = "S"
    MX = "FS"
    R270 = "E"
    MYR90 = "FE"

    def __str__(self) -> str:
        return self.value


@dataclass
class Instance:
    """
    Location information for an instance of a cell or macro.

    :param location: The physical co-ordinates of the object's origin. Leave
        empty for automatic placement.
    :param orientation: The orientation of the object's placement. 'N'/'R0' by default.
    """

    location: Optional[Tuple[Decimal, Decimal]]
    orientation: Optional[Orientation]


@dataclass
class Macro:
    """
    A data structure for storing definitions of Macros.

    As it is typically stored in a dictionary in its superclass, the module name
    is not stored in any of these fields.

    You will note most fields correspond to a :class:`openlane.state.DesignFormat`
    entry IDs. This is not coincidental.

    :param gds: A list of GDSII files representing the design. At least one is required.
    :param lef: A list of LEF files representing the design. At least one is required.
    :param instances: A dictionary of :class:`Instance` objects representing the
        instances of said macro.

        The keys for the dictionaries are the names of the instances.
    :param nl: A list of netlists constituting the design.

        The netlists must be valid Verilog netlists readable by tools such as
        OpenSTA.

        Can be empty, however SPEF-based hierarchical static timing analysis
        will be unavailable.
    :param spef: A dictionary of parasitics annotations for the various netlists of the
        Macro.

        The keys are wildcards for timing corners supported by a certain SPEF file.

        Can be empty, however SPEF-based hierarchical static timing analysis
        will be unavailable.
    :param lib: A dictionary of timing library files.

        The keys are wildcards for timing corners supported by a certain LIB file.

        If both SPEF and LIB views are empty, the design may be black-boxed
        during STA.
    :param spice: A list of SPICE netlists constituting the design. May be
        useful in some flows.
    :param sdf: A dictionary of standard delay format files. May be useful in some flows.

        The keys are wildcards for timing corners supported by a certain SPEF file.
    :param json_h: A JSON file as generated by Yosys. Helpful in some flows.
    """

    gds: List[Path]
    lef: List[Path]
    instances: Dict[str, Instance] = field(default_factory=lambda: {})

    vh: List[Path] = field(default_factory=lambda: [])
    nl: List[Path] = field(default_factory=lambda: [])
    pnl: List[Path] = field(default_factory=lambda: [])
    spef: Dict[str, List[Path]] = field(default_factory=lambda: {})
    lib: Dict[str, List[Path]] = field(default_factory=lambda: {})
    spice: List[Path] = field(default_factory=lambda: [])
    sdf: Dict[str, List[Path]] = field(default_factory=lambda: {})

    json_h: Optional[Path] = None

    def view_by_df(
        self, df: DesignFormat
    ) -> Union[None, Path, List[Path], Dict[str, List[Path]]]:
        try:
            return getattr(self, df.value.id)
        except AttributeError:
            return None

    def __post_init__(self):
        if len(self.gds) < 1:
            raise ValueError(
                "Macro definition invalid- at least one GDSII file must be specified."
            )
        if len(self.lef) < 1:
            raise ValueError(
                "Macro definition invalid- at least one LEF file must be specified."
            )

    def __repr__(self) -> str:
        return f"{self.__class__.__qualname__}(%s)" % ", ".join(
            [f"{k}={repr(v)}" for k, v in asdict(self).items()]
        )

    def __str__(self) -> str:
        return self.__repr__()

    @classmethod
    def from_state(Self, state: State) -> "Macro":
        kwargs = {}
        for macro_field in fields(Self):
            views = state.get(macro_field.name)
            if views is None:
                if macro_field.default_factory is not MISSING:
                    kwargs[macro_field.name] = macro_field.default_factory()
                elif macro_field.default is not MISSING:
                    kwargs[macro_field.name] = macro_field.default
                else:  # gds or lef
                    raise ValueError(
                        f"Macro cannot be made out of input state: View {macro_field.name} is missing"
                    )
                continue
            var_name = f"{Self.__name__}.{macro_field.name}"
            _, final = Variable(var_name, macro_field.type, "").compile(
                GenericDict({var_name: views}),
                warning_list_ref=[],
                permissive_typing=True,
            )
            kwargs[macro_field.name] = final

        return Self(**kwargs)  # type: ignore

    def instantiate(
        self,
        instance_name: str,
        location: Tuple[Number, Number],
        orientation: Orientation = Orientation.N,
    ):
        location = (Decimal(location[0]), Decimal(location[1]))
        self.instances[instance_name] = Instance(location, Orientation[orientation])


def is_optional(t: Type[Any]) -> bool:
    type_args = get_args(t)
    return get_origin(t) is Union and type(None) in type_args


def some_of(t: Type[Any]) -> Type[Any]:
    if not is_optional(t):
        return t

    # t must be a Union with None if we're here

    type_args = get_args(t)

    args_without_none = [arg for arg in type_args if arg != type(None)]
    if len(args_without_none) == 1:
        return args_without_none[0]

    new_union = Union[tuple(args_without_none)]  # type: ignore
    return new_union  # type: ignore


def repr_type(t: Type[Any]) -> str:  # pragma: no cover
    optional = is_optional(t)
    some = some_of(t)

    if hasattr(some, "__name__"):  # Python 3.10+
        type_string = some.__name__
    else:
        type_string = str(some)

    if inspect.isclass(some) and issubclass(some, Enum):
        type_string = "｜".join([str(e.name) for e in some])
        type_string = f"`{type_string}`"
    else:
        origin, args = get_origin(some), get_args(some)
        if origin is not None:
            if origin == Union:
                arg_strings = [repr_type(arg) for arg in args]
                type_string = "｜".join(arg_strings)
                type_string = f"({type_string})"
            elif origin == Literal:
                return "｜".join([repr(arg) for arg in args])
            else:
                arg_strings = [repr_type(arg) for arg in args]
                type_string = f"{type_string}[{', '.join(arg_strings)}]"

    return type_string + ("?" if optional else "")


@dataclass
class Variable:
    """
    An object encapsulating metadata on an OpenLane configuration variable, which
    is used to name, document and validate values supplied to
    :class:`openlane.steps.Step`\\s or :class:`openlane.flows.Flow`\\s.

    Values supplied for configuration variables are the primary interface by
    which users configure OpenLane flows.

    :param name: A string name for the Variable. Because of backwards compatibility
        with OpenLane 1, the convention is ``UPPER_SNAKE_CASE``.

    :param type: A Python type object representing the variable.

        Supported scalars:

        - ``int``
        - ``decimal.Decimal``
        - ``bool``
        - ``str``
        - :class:`Path`

        Supported products:

        - ``Union`` (incl. ``Optional``)
        - ``List``
        - ``Tuple``
        - ``Dict``
        - ``Enum``

        Other:

        - ``dataclass`` types composed of the above.

    :param description: A human-readable description of the variable. Used to
        generate help strings and documentation.

    :param default: A default value for the variable.

        Optional variables have an implicit default value of ``None``.

    :param deprecated_names: A list of deprecated names for said variable.

        An element of the list can alternative be a tuple of a name and a Callable
        used to perform a translation for when a renamed variable is also slightly
        modified.

    :param units: Used only in documentation: the unit corresponding to this
        object, i.e., µm, pF, etc. Can be any string, but for consistency, SI units
        must be represented in terms of their official symbols.

    :param pdk: Whether this variable is expected to be given a default value
        by a PDK or not.

        If this is true, and the variable is not of an option type, a PDK *must*
        give this variable a default value in order to be marked compatible
        with a step.

        If this is true and the variable is of an option type, a PDK may
        optionally provide a default value for this variable, however steps
        must presume it is ``null``.

        If this is false, a PDK is not allowed to set a default value for
        this variable. In current versions of OpenLane, the value will be
        silently ignored, but warnings or errors may occur in future versions.
    """

    known_variable_names: ClassVar[Set[str]] = set()

    name: str
    type: Any
    description: str
    default: Any = None
    deprecated_names: List[Union[str, Tuple[str, Callable]]] = field(
        default_factory=list
    )

    units: Optional[str] = None
    pdk: bool = False

    def __post_init__(self):
        Variable.known_variable_names.add(self.name)
        for name in self.deprecated_names:
            if isinstance(name, tuple):
                name, _ = name
            Variable.known_variable_names.add(name)

    @property
    def optional(self) -> bool:
        """
        :returns: Whether a variable's type is an `Option type <https://en.wikipedia.org/wiki/Option_type>`_.
        """
        return is_optional(self.type)

    @property
    def some(self) -> Any:
        """
        :returns: The type of a variable presuming it is not None.

            If a variable is not Optional, that is simply the type specified in the
            ``type`` attribute.
        """
        return some_of(self.type)

    def type_repr_md(self, for_document: bool = False) -> str:  # pragma: no cover
        """
        :param for_document: Adds HTML line breaks between sum type separators
            for easier wrapping by web browsers/PDF renderers/what have you
        :returns: A pretty Markdown string representation of the Variable's type.
        """
        if for_document:
            return repr_type(self.type).replace("｜", "｜<br />")
        return repr_type(self.type)

    def desc_repr_md(self) -> str:  # pragma: no cover
        """
        :returns: The description, but with newlines escaped for Markdown.
        """
        return self.description.replace("\n", "<br />")

    def __process(
        self,
        key_path: str,
        value: Any,
        validating_type: Type[Any],
        default: Any = None,
        explicitly_specified: bool = True,
        permissive_typing: bool = False,
        depth: int = 0,
    ):
        if value is None:
            if explicitly_specified:
                # User explicitly specified "null" for this value: only error if
                # value is not optional
                if not is_optional(validating_type):
                    raise ValueError(
                        f"Non-optional variable '{key_path}' explicitly assigned a null value."
                    )
                else:
                    return None
            else:
                # User did not specify a value for this variable: couple outcomes
                if default is not None:
                    return self.__process(
                        key_path=key_path,
                        value=default,
                        validating_type=validating_type,
                        permissive_typing=permissive_typing,
                        depth=depth + 1,
                    )
                elif not is_optional(validating_type):
                    if depth == 0:
                        raise MissingRequiredVariable(self, self.pdk)
                    else:
                        raise ValueError(f"'{key_path}' must be non-null.")
                else:
                    return None

        if is_optional(validating_type):
            validating_type = some_of(validating_type)

        type_origin = get_origin(validating_type)
        type_args = get_args(validating_type)

        if type_origin in [list, tuple]:
            return_value = list()
            raw = value
            if isinstance(raw, list) or isinstance(raw, tuple):
                pass
            elif is_string(raw):
                if not permissive_typing:
                    raise ValueError(
                        f"Refusing to automatically convert string at '{key_path}' to list"
                    )
                if "," in raw:
                    raw = raw.split(",")
                elif ";" in raw:
                    raw = raw.split(";")
                else:
                    raw = raw.split()
                if len(raw) and raw[-1] == "":
                    raw.pop()  # Trailing commas
            else:
                raise ValueError(
                    f"List provided for variable '{key_path}' is invalid: {value}"
                )

            if type_origin == tuple:
                if len(raw) != len(type_args):
                    raise ValueError(
                        f"Value provided for variable '{key_path}' of type {validating_type} is invalid: ({len(raw)}/{len(type_args)}) tuple entries provided"
                    )

            for i, (item, value_type) in enumerate(
                zip_first(raw, type_args, fillvalue=type_args[0])
            ):
                return_value.append(
                    self.__process(
                        key_path=f"{key_path}[{i}]",
                        value=item,
                        validating_type=value_type,
                        permissive_typing=permissive_typing,
                        depth=depth + 1,
                    )
                )

            if type_origin == tuple:
                return tuple(return_value)

            return return_value
        elif type_origin == dict:
            raw = value
            key_type, value_type = type_args
            if isinstance(raw, dict):
                pass
            elif isinstance(raw, list) or is_string(raw):
                if not permissive_typing:
                    raise ValueError(
                        f"Refusing to automatically convert string at '{key_path}' to dict"
                    )
                components = raw
                if is_string(raw):
                    components = shlex.split(raw)
                assert isinstance(components, list)
                # Assuming Tcl format:
                if len(components) % 2 != 0:
                    raise ValueError(
                        f"Tcl-style flat dictionary provided for variable '{key_path}' is invalid: uneven number of components ({len(components)})"
                    )
                raw = {}
                for i in range(0, len(components) // 2):
                    key = components[2 * i]
                    val = components[2 * i + 1]
                    raw[key] = val
            else:
                raise ValueError(
                    f"Value provided for variable '{key_path}' of type {validating_type} is invalid: '{value}'"
                )

            processed = {}
            for key, val in raw.items():
                key_validated = self.__process(
                    key_path=key_path,
                    value=key,
                    validating_type=key_type,
                    permissive_typing=permissive_typing,
                    depth=depth + 1,
                )
                value_validated = self.__process(
                    key_path=f"{key_path}.{key_validated}",
                    value=val,
                    validating_type=value_type,
                    permissive_typing=permissive_typing,
                    depth=depth + 1,
                )
                processed[key_validated] = value_validated

            return processed
        elif type_origin == Union:
            final_value = None
            errors = []
            for arg in type_args:
                try:
                    final_value = self.__process(
                        key_path=key_path,
                        value=value,
                        validating_type=arg,
                        permissive_typing=permissive_typing,
                        depth=depth + 1,
                    )
                    if final_value is not None:
                        return final_value
                except ValueError as e:
                    errors.append(f"\t{str(e)}")
            raise ValueError(
                "\n".join(
                    [
                        f"Value for '{key_path}' is invalid for union {repr_type(validating_type)}:"
                    ]
                    + errors
                )
            )
        elif type_origin == Literal:
            if value in type_args:
                return value
            else:
                raise ValueError(
                    f"Value for '{key_path}' is invalid for {repr_type(validating_type)}: '{value}'"
                )
        elif is_dataclass(validating_type):
            if isinstance(value, validating_type):
                # Do not validate further
                return value

            raw = value
            if not isinstance(raw, dict):
                raise ValueError(
                    f"Value provided for deserializable class {validating_type} at '{key_path}' is not a dictionary."
                )
            raw = value.copy()
            kwargs_dict = {}
            for current_field in fields(validating_type):
                key = current_field.name
                subtype = current_field.type
                explicitly_specified = False
                if key in raw:
                    explicitly_specified = True
                field_value = raw.get(key)
                field_default = None
                if (
                    current_field.default is not None
                    and type(current_field.default) != _MISSING_TYPE
                ):
                    field_default = current_field.default
                if current_field.default_factory != MISSING:
                    field_default = current_field.default_factory()
                value__processed = self.__process(
                    key_path=f"{key_path}.{key}",
                    value=field_value,
                    explicitly_specified=explicitly_specified,
                    default=field_default,
                    validating_type=subtype,
                    permissive_typing=permissive_typing,
                    depth=depth + 1,
                )
                kwargs_dict[key] = value__processed
                if explicitly_specified:
                    del raw[key]
            if len(raw):
                raise ValueError(
                    f"One or more keys unrecognized for dataclass {validating_type.__qualname__}: {' '.join(raw.keys())}"
                )
            return validating_type(**kwargs_dict)
        elif validating_type == Path:
            # Handle one-file globs
            if isinstance(value, list) and len(value) == 1:
                value = value[0]
            result = Path(value)
            result.validate(f"Path provided for variable '{key_path}' is invalid")
            return result
        elif validating_type == bool:
            if not permissive_typing and not isinstance(value, bool):
                raise ValueError(
                    f"Refusing to automatically convert '{value}' at '{key_path}' to a Boolean"
                )
            if value in ["1", "true", "True", 1, True]:
                return True
            elif value in ["0", "false", "False", 0, False]:
                return False
            else:
                raise ValueError(
                    f"Value provided for variable '{key_path}' of type {validating_type.__name__} is invalid: '{value}'"
                )
        elif issubclass(validating_type, Enum):
            if type(value) == validating_type:
                return value
            try:
                return validating_type[value]
            except KeyError:
                raise ValueError(
                    f"Variable provided for variable '{key_path}' of enumerated type {validating_type.__name__} is invalid: '{value}'"
                )
        elif issubclass(validating_type, str):
            if not is_string(value):
                raise ValueError(
                    f"Refusing to automatically convert value at '{key_path}' to a string"
                )
            return str(value)
        elif issubclass(validating_type, Decimal) or issubclass(validating_type, int):
            try:
                final = validating_type(value)
            except (InvalidOperation, TypeError):
                raise ValueError(
                    f"Value provided for variable '{key_path}' of type {validating_type.__name__} is invalid: '{value}'"
                )
            if not permissive_typing and not (
                isinstance(value, int)
                or isinstance(value, float)
                or isinstance(value, Decimal)
            ):
                raise ValueError(
                    f"Refusing to automatically convert value at '{key_path}' to a {validating_type.__name__}"
                )
            return final

        else:
            try:
                return validating_type(value)
            except ValueError as e:
                raise ValueError(
                    f"Value provided for variable '{key_path}' of type {validating_type.__name__} is invalid: '{value}' {e}"
                )

    def compile(
        self,
        mutable_config: GenericDict[str, Any],
        warning_list_ref: List[str],
        values_so_far: Optional[Mapping[str, Any]] = None,
        permissive_typing: bool = False,
    ) -> Tuple[Optional[str], Any]:
        exists: Optional[str] = None
        value: Optional[Any] = None

        i = 0
        while (
            not exists
            and self.deprecated_names is not None
            and i < len(self.deprecated_names)
        ):
            deprecated_name = self.deprecated_names[i]
            deprecated_callable = lambda x: x
            if not isinstance(deprecated_name, str):
                deprecated_name, deprecated_callable = deprecated_name
            exists, value = mutable_config.check(deprecated_name)
            if exists:
                warning_list_ref.append(
                    f"The configuration variable '{deprecated_name}' is deprecated. Please check the docs for the usage on the replacement variable '{self.name}'."
                )
            if value is not None:
                value = deprecated_callable(value)
            i = i + 1

        if not exists:
            exists, value = mutable_config.check(self.name)

        processed = self.__process(
            key_path=self.name,
            value=value,
            default=self.default,
            validating_type=self.type,
            explicitly_specified=exists is not None,
            permissive_typing=permissive_typing,
        )

        return (exists, processed)

    def _get_docs_identifier(self, parent: Optional[str] = None) -> str:
        identifier = f"var-{self.name.lower()}"
        if parent is not None:
            identifier = f"var-{slugify(parent)}-{self.name.lower()}"
        return identifier

    def __hash__(self) -> int:
        return hash((self.name, self.type, self.default))

    def __eq__(self, rhs: object) -> bool:
        if not isinstance(rhs, Variable):
            raise NotImplementedError()
        return (
            self.name == rhs.name
            and self.type == rhs.type
            and self.default == rhs.default
        )