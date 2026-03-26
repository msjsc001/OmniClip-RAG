from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANIFESTS_DIR = ROOT / "manifests"


def pkg(
    name: str,
    version: str,
    filename: str,
    sha256: str,
    *,
    source_key: str = "pypi",
    requirement: str | None = None,
) -> dict[str, str]:
    return {
        "name": name,
        "version": version,
        "requirement": requirement or f"{name}=={version}",
        "filename": filename,
        "sha256": sha256,
        "source_key": source_key,
    }


ARTIFACTS: dict[str, dict[str, str]] = {
    "annotated_types_0_7_0": pkg(
        "annotated-types",
        "0.7.0",
        "annotated_types-0.7.0-py3-none-any.whl",
        "1f02e8b43a8fbbc3f3e0d4f0f4bfc8131bcb4eebe8849b8e5c773f3a1c582a53",
    ),
    "certifi_2026_2_25": pkg(
        "certifi",
        "2026.2.25",
        "certifi-2026.2.25-py3-none-any.whl",
        "027692e4402ad994f1c42e52a4997a9763c646b73e4096e4d5d6db8af1d6f0fa",
    ),
    "charset_normalizer_3_4_6": pkg(
        "charset-normalizer",
        "3.4.6",
        "charset_normalizer-3.4.6-cp313-cp313-win_amd64.whl",
        "572d7c822caf521f0525ba1bce1a622a0b85cf47ffbdae6c9c19e3b5ac3c4389",
    ),
    "colorama_0_4_6": pkg(
        "colorama",
        "0.4.6",
        "colorama-0.4.6-py2.py3-none-any.whl",
        "4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6",
    ),
    "deprecation_2_1_0": pkg(
        "deprecation",
        "2.1.0",
        "deprecation-2.1.0-py2.py3-none-any.whl",
        "a10811591210e1fb0e768a8c25517cabeabcba6f0bf96564f8ff45189f90b14a",
    ),
    "filelock_3_25_2": pkg(
        "filelock",
        "3.25.2",
        "filelock-3.25.2-py3-none-any.whl",
        "ca8afb0da15f229774c9ad1b455ed96e85a81373065fb10446672f64444ddf70",
    ),
    "flatbuffers_25_12_19": pkg(
        "flatbuffers",
        "25.12.19",
        "flatbuffers-25.12.19-py2.py3-none-any.whl",
        "7634f50c427838bb021c2d66a3d1168e9d199b0607e6329399f04846d42e20b4",
    ),
    "fsspec_2026_2_0": pkg(
        "fsspec",
        "2026.2.0",
        "fsspec-2026.2.0-py3-none-any.whl",
        "98de475b5cb3bd66bedd5c4679e87b4fdfe1a3bf4d707b151b3c07e58c9a2437",
    ),
    "huggingface_hub_0_36_0": pkg(
        "huggingface-hub",
        "0.36.0",
        "huggingface_hub-0.36.0-py3-none-any.whl",
        "7bcc9ad17d5b3f07b57c78e79d527102d08313caa278a641993acddcb894548d",
    ),
    "idna_3_11": pkg(
        "idna",
        "3.11",
        "idna-3.11-py3-none-any.whl",
        "771a87f49d9defaf64091e6e6fe9c18d4833f140bd19464795bc32d966ca37ea",
    ),
    "jinja2_3_1_6": pkg(
        "Jinja2",
        "3.1.6",
        "jinja2-3.1.6-py3-none-any.whl",
        "85ece4451f492d0c13c5dd7c13a64681a86afae63a5f347908daf103ce6d2f67",
    ),
    "joblib_1_5_3": pkg(
        "joblib",
        "1.5.3",
        "joblib-1.5.3-py3-none-any.whl",
        "5fc3c5039fc5ca8c0276333a188bbd59d6b7ab37fe6632daa76bc7f9ec18e713",
    ),
    "lance_namespace_0_6_1": pkg(
        "lance-namespace",
        "0.6.1",
        "lance_namespace-0.6.1-py3-none-any.whl",
        "9699c9e3f12236e5e08ea979cc4e036a8e3c67ed2f37ae6f25c5353ab908e1be",
    ),
    "lance_namespace_urllib3_client_0_6_1": pkg(
        "lance-namespace-urllib3-client",
        "0.6.1",
        "lance_namespace_urllib3_client-0.6.1-py3-none-any.whl",
        "b9c103e1377ad46d2bd70eec894bfec0b1e2133dae0964d7e4de543c6e16293b",
    ),
    "lancedb_0_29_0": pkg(
        "lancedb",
        "0.29.0",
        "lancedb-0.29.0-cp39-abi3-win_amd64.whl",
        "d33ff1bc304b1ff8aebb10e1e9a8aee1cd95ba8791d1efcb3a4b02b679681732",
    ),
    "markupsafe_3_0_3": pkg(
        "MarkupSafe",
        "3.0.3",
        "markupsafe-3.0.3-cp313-cp313-win_amd64.whl",
        "9a1abfdc021a164803f4d485104931fb8f8c1efd55bc6b748d2f5774e78b62c5",
    ),
    "mpmath_1_3_0": pkg(
        "mpmath",
        "1.3.0",
        "mpmath-1.3.0-py3-none-any.whl",
        "a0b2b9fe80bbcd81a6647ff13108738cfb482d481d826cc0e02f5b35e5c88d2c",
    ),
    "networkx_3_6_1": pkg(
        "networkx",
        "3.6.1",
        "networkx-3.6.1-py3-none-any.whl",
        "d47fbf302e7d9cbbb9e2555a0d267983d2aa476bac30e90dfbe5669bd57f3762",
    ),
    "numpy_2_1_3": pkg(
        "numpy",
        "2.1.3",
        "numpy-2.1.3-cp313-cp313-win_amd64.whl",
        "747641635d3d44bcb380d950679462fae44f54b131be347d5ec2bce47d3df9ed",
    ),
    "numpy_2_4_3": pkg(
        "numpy",
        "2.4.3",
        "numpy-2.4.3-cp313-cp313-win_amd64.whl",
        "0a60e17a14d640f49146cb38e3f105f571318db7826d9b6fef7e4dce758faecd",
    ),
    "onnxruntime_1_24_4": pkg(
        "onnxruntime",
        "1.24.4",
        "onnxruntime-1.24.4-cp313-cp313-win_amd64.whl",
        "3b6ba8b0181a3aa88edab00eb01424ffc06f42e71095a91186c2249415fcff93",
    ),
    "packaging_26_0": pkg(
        "packaging",
        "26.0",
        "packaging-26.0-py3-none-any.whl",
        "b36f1fef9334a5588b4166f8bcd26a14e521f2b55e6b9de3aaa80d3ff7a37529",
    ),
    "pandas_2_2_3": pkg(
        "pandas",
        "2.2.3",
        "pandas-2.2.3-cp313-cp313-win_amd64.whl",
        "61c5ad4043f791b61dd4752191d9f07f0ae412515d59ba8f005832a532f8736d",
    ),
    "pillow_12_1_1": pkg(
        "pillow",
        "12.1.1",
        "pillow-12.1.1-cp313-cp313-win_amd64.whl",
        "344cf1e3dab3be4b1fa08e449323d98a2a3f819ad20f4b22e77a0ede31f0faa1",
    ),
    "protobuf_7_34_1": pkg(
        "protobuf",
        "7.34.1",
        "protobuf-7.34.1-cp310-abi3-win_amd64.whl",
        "e97b55646e6ce5cbb0954a8c28cd39a5869b59090dfaa7df4598a7fba869468c",
    ),
    "pyarrow_20_0_0": pkg(
        "pyarrow",
        "20.0.0",
        "pyarrow-20.0.0-cp313-cp313-win_amd64.whl",
        "30b3051b7975801c1e1d387e17c588d8ab05ced9b1e14eec57915f79869b5031",
    ),
    "pydantic_2_12_5": pkg(
        "pydantic",
        "2.12.5",
        "pydantic-2.12.5-py3-none-any.whl",
        "e561593fccf61e8a20fc46dfc2dfe075b8be7d0188df33f221ad1f0139180f9d",
    ),
    "pydantic_core_2_41_5": pkg(
        "pydantic-core",
        "2.41.5",
        "pydantic_core-2.41.5-cp313-cp313-win_amd64.whl",
        "79ec52ec461e99e13791ec6508c722742ad745571f234ea6255bed38c6480f11",
    ),
    "python_dateutil_2_9_0_post0": pkg(
        "python-dateutil",
        "2.9.0.post0",
        "python_dateutil-2.9.0.post0-py2.py3-none-any.whl",
        "a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427",
    ),
    "pytz_2026_1_post1": pkg(
        "pytz",
        "2026.1.post1",
        "pytz-2026.1.post1-py2.py3-none-any.whl",
        "f2fd16142fda348286a75e1a524be810bb05d444e5a081f37f7affc635035f7a",
    ),
    "pyyaml_6_0_3": pkg(
        "PyYAML",
        "6.0.3",
        "pyyaml-6.0.3-cp313-cp313-win_amd64.whl",
        "79005a0d97d5ddabfeeea4cf676af11e647e41d81c9a7722a193022accdb6b7c",
    ),
    "regex_2026_2_28": pkg(
        "regex",
        "2026.2.28",
        "regex-2026.2.28-cp313-cp313-win_amd64.whl",
        "71a911098be38c859ceb3f9a9ce43f4ed9f4c6720ad8684a066ea246b76ad9ff",
    ),
    "requests_2_33_0": pkg(
        "requests",
        "2.33.0",
        "requests-2.33.0-py3-none-any.whl",
        "3324635456fa185245e24865e810cecec7b4caf933d7eb133dcde67d48cee69b",
    ),
    "safetensors_0_6_2": pkg(
        "safetensors",
        "0.6.2",
        "safetensors-0.6.2-cp38-abi3-win_amd64.whl",
        "c7b214870df923cbc1593c3faee16bec59ea462758699bd3fee399d00aac072c",
    ),
    "scikit_learn_1_8_0": pkg(
        "scikit-learn",
        "1.8.0",
        "scikit_learn-1.8.0-cp313-cp313-win_amd64.whl",
        "2de443b9373b3b615aec1bb57f9baa6bb3a9bd093f1269ba95c17d870422b271",
    ),
    "scipy_1_15_2": pkg(
        "scipy",
        "1.15.2",
        "scipy-1.15.2-cp313-cp313-win_amd64.whl",
        "a5080a79dfb9b78b768cebf3c9dcbc7b665c5875793569f48bf0e2b1d7f68f6f",
    ),
    "sentence_transformers_5_1_2": pkg(
        "sentence-transformers",
        "5.1.2",
        "sentence_transformers-5.1.2-py3-none-any.whl",
        "724ce0ea62200f413f1a5059712aff66495bc4e815a1493f7f9bca242414c333",
    ),
    "setuptools_82_0_1": pkg(
        "setuptools",
        "82.0.1",
        "setuptools-82.0.1-py3-none-any.whl",
        "a59e362652f08dcd477c78bb6e7bd9d80a7995bc73ce773050228a348ce2e5bb",
    ),
    "six_1_17_0": pkg(
        "six",
        "1.17.0",
        "six-1.17.0-py2.py3-none-any.whl",
        "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274",
    ),
    "sympy_1_14_0": pkg(
        "sympy",
        "1.14.0",
        "sympy-1.14.0-py3-none-any.whl",
        "e091cc3e99d2141a0ba2847328f5479b05d94a6635cb96148ccb3f34671bd8f5",
    ),
    "threadpoolctl_3_6_0": pkg(
        "threadpoolctl",
        "3.6.0",
        "threadpoolctl-3.6.0-py3-none-any.whl",
        "43a0b8fd5a2928500110039e43a5eed8480b918967083ea48dc3ab9f13c4a7fb",
    ),
    "tokenizers_0_22_2": pkg(
        "tokenizers",
        "0.22.2",
        "tokenizers-0.22.2-cp39-abi3-win_amd64.whl",
        "c9ea31edff2968b44a88f97d784c2f16dc0729b8b143ed004699ebca91f05c48",
    ),
    "torch_cpu_2_10_0": pkg(
        "torch",
        "2.10.0+cpu",
        "torch-2.10.0+cpu-cp313-cp313-win_amd64.whl",
        "b719da5af01b59126ac13eefd6ba3dd12d002dc0e8e79b8b365e55267a8189d3",
        source_key="pytorch",
    ),
    "torch_cuda_2_10_0": pkg(
        "torch",
        "2.10.0+cu128",
        "torch-2.10.0+cu128-cp313-cp313-win_amd64.whl",
        "4d1b0b49c54223c7c04050b49eac141d77b6edbc34aea1dfc74a6fdb661baa8c",
        source_key="pytorch",
    ),
    "tqdm_4_67_3": pkg(
        "tqdm",
        "4.67.3",
        "tqdm-4.67.3-py3-none-any.whl",
        "ee1e4c0e59148062281c49d80b25b67771a127c85fc9676d3be5f243206826bf",
    ),
    "transformers_4_57_2": pkg(
        "transformers",
        "4.57.2",
        "transformers-4.57.2-py3-none-any.whl",
        "0918df354853c9931a637792cec519e137aceb150effd4c7924d6b8d36918fab",
    ),
    "typing_extensions_4_15_0": pkg(
        "typing-extensions",
        "4.15.0",
        "typing_extensions-4.15.0-py3-none-any.whl",
        "f0fa19c6845758ab08074a0cfa8b7aecb71c999ca73d62883bc25cc018c4e548",
    ),
    "typing_inspection_0_4_2": pkg(
        "typing-inspection",
        "0.4.2",
        "typing_inspection-0.4.2-py3-none-any.whl",
        "4ed1cacbdc298c220f1bd249ed5287caa16f34d44ef4e9c3d0cbad5b521545e7",
    ),
    "tzdata_2025_3": pkg(
        "tzdata",
        "2025.3",
        "tzdata-2025.3-py2.py3-none-any.whl",
        "06a47e5700f3081aab02b2e513160914ff0694bce9947d6b76ebd6bf57cfc5d1",
    ),
    "urllib3_2_6_3": pkg(
        "urllib3",
        "2.6.3",
        "urllib3-2.6.3-py3-none-any.whl",
        "bf272323e553dfb2e87d9bfd225ca7b0f467b919d7bbd355436d3fd37cb0acd4",
    ),
}


SEMANTIC_BASE: list[str] = [
    "certifi_2026_2_25",
    "charset_normalizer_3_4_6",
    "colorama_0_4_6",
    "filelock_3_25_2",
    "fsspec_2026_2_0",
    "huggingface_hub_0_36_0",
    "idna_3_11",
    "jinja2_3_1_6",
    "joblib_1_5_3",
    "markupsafe_3_0_3",
    "mpmath_1_3_0",
    "networkx_3_6_1",
    "numpy_2_1_3",
    "packaging_26_0",
    "pillow_12_1_1",
    "pyyaml_6_0_3",
    "regex_2026_2_28",
    "requests_2_33_0",
    "safetensors_0_6_2",
    "scikit_learn_1_8_0",
    "scipy_1_15_2",
    "sentence_transformers_5_1_2",
    "setuptools_82_0_1",
    "sympy_1_14_0",
    "threadpoolctl_3_6_0",
    "tokenizers_0_22_2",
    "tqdm_4_67_3",
    "transformers_4_57_2",
    "typing_extensions_4_15_0",
    "urllib3_2_6_3",
]
CPU_SEMANTIC: list[str] = ["torch_cpu_2_10_0", *SEMANTIC_BASE]
CUDA_SEMANTIC: list[str] = ["torch_cuda_2_10_0", *SEMANTIC_BASE]
VECTOR_EXTRAS: list[str] = [
    "annotated_types_0_7_0",
    "deprecation_2_1_0",
    "flatbuffers_25_12_19",
    "lance_namespace_0_6_1",
    "lance_namespace_urllib3_client_0_6_1",
    "lancedb_0_29_0",
    "onnxruntime_1_24_4",
    "pandas_2_2_3",
    "protobuf_7_34_1",
    "pyarrow_20_0_0",
    "pydantic_2_12_5",
    "pydantic_core_2_41_5",
    "python_dateutil_2_9_0_post0",
    "pytz_2026_1_post1",
    "six_1_17_0",
    "typing_inspection_0_4_2",
    "tzdata_2025_3",
]
CPU_VECTOR: list[str] = [
    "annotated_types_0_7_0",
    "colorama_0_4_6",
    "deprecation_2_1_0",
    "flatbuffers_25_12_19",
    "lance_namespace_0_6_1",
    "lance_namespace_urllib3_client_0_6_1",
    "lancedb_0_29_0",
    "numpy_2_4_3",
    "onnxruntime_1_24_4",
    "packaging_26_0",
    "pandas_2_2_3",
    "protobuf_7_34_1",
    "pyarrow_20_0_0",
    "pydantic_2_12_5",
    "pydantic_core_2_41_5",
    "python_dateutil_2_9_0_post0",
    "pytz_2026_1_post1",
    "six_1_17_0",
    "tqdm_4_67_3",
    "typing_extensions_4_15_0",
    "typing_inspection_0_4_2",
    "tzdata_2025_3",
    "urllib3_2_6_3",
]
CUDA_VECTOR: list[str] = list(CPU_VECTOR)
CPU_ALL: list[str] = CPU_SEMANTIC + [item for item in VECTOR_EXTRAS if item not in set(CPU_SEMANTIC)]
CUDA_ALL: list[str] = CUDA_SEMANTIC + [item for item in VECTOR_EXTRAS if item not in set(CUDA_SEMANTIC)]

CPU_PROFILE_SOURCES: dict[str, dict[str, dict[str, object]]] = {
    "official": {
        "pytorch": {
            "index_url": "https://download.pytorch.org/whl/cpu",
            "extra_index_urls": ["https://pypi.org/simple"],
        },
        "pypi": {"index_url": "https://pypi.org/simple", "extra_index_urls": []},
    },
    "mirror": {
        "pytorch": {
            "index_url": "https://download.pytorch.org/whl/cpu",
            "extra_index_urls": ["https://pypi.tuna.tsinghua.edu.cn/simple"],
        },
        "pypi": {"index_url": "https://pypi.tuna.tsinghua.edu.cn/simple", "extra_index_urls": []},
    },
}
CUDA_PROFILE_SOURCES: dict[str, dict[str, dict[str, object]]] = {
    "official": {
        "pytorch": {
            "index_url": "https://download.pytorch.org/whl/cu128",
            "extra_index_urls": ["https://pypi.org/simple"],
        },
        "pypi": {"index_url": "https://pypi.org/simple", "extra_index_urls": []},
    },
    "mirror": {
        "pytorch": {
            "index_url": "https://download.pytorch.org/whl/cu128",
            "extra_index_urls": ["https://pypi.tuna.tsinghua.edu.cn/simple"],
        },
        "pypi": {"index_url": "https://pypi.tuna.tsinghua.edu.cn/simple", "extra_index_urls": []},
    },
}
VECTOR_SOURCES: dict[str, dict[str, dict[str, object]]] = {
    "official": {"pypi": {"index_url": "https://pypi.org/simple", "extra_index_urls": []}},
    "mirror": {"pypi": {"index_url": "https://pypi.tuna.tsinghua.edu.cn/simple", "extra_index_urls": []}},
}

SEMANTIC_REQUIREMENTS_CPU = [
    {"name": "torch", "version": "2.10.0+cpu", "requirement": "torch==2.10.0+cpu", "source_key": "pytorch"},
    {"name": "numpy", "version": "2.1.3", "requirement": "numpy==2.1.3", "source_key": "pypi"},
    {"name": "scipy", "version": "1.15.2", "requirement": "scipy==1.15.2", "source_key": "pypi"},
    {"name": "sentence-transformers", "version": "5.1.2", "requirement": "sentence-transformers==5.1.2", "source_key": "pypi"},
    {"name": "transformers", "version": "4.57.2", "requirement": "transformers==4.57.2", "source_key": "pypi"},
    {"name": "huggingface-hub", "version": "0.36.0", "requirement": "huggingface-hub==0.36.0", "source_key": "pypi"},
    {"name": "safetensors", "version": "0.6.2", "requirement": "safetensors==0.6.2", "source_key": "pypi"},
]
SEMANTIC_REQUIREMENTS_CUDA = [
    {"name": "torch", "version": "2.10.0+cu128", "requirement": "torch==2.10.0+cu128", "source_key": "pytorch"},
    *SEMANTIC_REQUIREMENTS_CPU[1:],
]
VECTOR_REQUIREMENTS = [
    {"name": "lancedb", "version": "0.29.0", "requirement": "lancedb==0.29.0", "source_key": "pypi"},
    {"name": "onnxruntime", "version": "1.24.4", "requirement": "onnxruntime==1.24.4", "source_key": "pypi"},
    {"name": "pyarrow", "version": "20.0.0", "requirement": "pyarrow==20.0.0", "source_key": "pypi"},
    {"name": "pandas", "version": "2.2.3", "requirement": "pandas==2.2.3", "source_key": "pypi"},
]
SEMANTIC_CLEANUP = [
    "torch", "torch-*dist-info", "functorch", "functorch-*dist-info", "torchgen", "torchgen-*dist-info",
    "numpy", "numpy-*dist-info", "numpy.libs",
    "scipy", "scipy-*dist-info", "scipy.libs",
    "sentence_transformers", "sentence_transformers-*dist-info",
    "transformers", "transformers-*dist-info",
    "huggingface_hub", "huggingface_hub-*dist-info",
    "safetensors", "safetensors-*dist-info",
]
VECTOR_CLEANUP = [
    "lancedb", "lancedb-*dist-info",
    "onnxruntime", "onnxruntime-*dist-info",
    "pyarrow", "pyarrow-*dist-info", "pyarrow.libs",
    "pandas", "pandas-*dist-info",
]
ALL_CLEANUP = [*SEMANTIC_CLEANUP, *VECTOR_CLEANUP]
SEMANTIC_REQUIRED = ["torch", "numpy", "scipy", "sentence_transformers", "transformers", "huggingface_hub", "safetensors"]
VECTOR_REQUIRED = ["lancedb", "onnxruntime", "pyarrow", "pandas"]
ALL_REQUIRED = [*SEMANTIC_REQUIRED, *VECTOR_REQUIRED]

MANIFESTS: dict[tuple[str, str], dict[str, object]] = {
    ("cpu", "semantic-core"): {
        "requirements": SEMANTIC_REQUIREMENTS_CPU,
        "artifacts": CPU_SEMANTIC,
        "source_profiles": CPU_PROFILE_SOURCES,
        "cleanup_patterns": SEMANTIC_CLEANUP,
        "required_modules": SEMANTIC_REQUIRED,
        "validation_probes": SEMANTIC_REQUIRED,
    },
    ("cpu", "compute-core"): {
        "requirements": SEMANTIC_REQUIREMENTS_CPU[:3],
        "artifacts": CPU_SEMANTIC,
        "source_profiles": CPU_PROFILE_SOURCES,
        "cleanup_patterns": SEMANTIC_CLEANUP,
        "required_modules": ["torch", "numpy", "scipy"],
        "validation_probes": ["torch", "numpy", "scipy"],
    },
    ("cpu", "model-stack"): {
        "requirements": SEMANTIC_REQUIREMENTS_CPU[3:],
        "artifacts": CPU_SEMANTIC,
        "source_profiles": CPU_PROFILE_SOURCES,
        "cleanup_patterns": SEMANTIC_CLEANUP,
        "required_modules": ["sentence_transformers", "transformers", "huggingface_hub", "safetensors"],
        "validation_probes": ["sentence_transformers", "transformers", "huggingface_hub", "safetensors"],
    },
    ("cpu", "vector-store"): {
        "requirements": VECTOR_REQUIREMENTS,
        "artifacts": CPU_VECTOR,
        "source_profiles": VECTOR_SOURCES,
        "cleanup_patterns": VECTOR_CLEANUP,
        "required_modules": VECTOR_REQUIRED,
        "validation_probes": VECTOR_REQUIRED,
    },
    ("cpu", "all"): {
        "requirements": [*SEMANTIC_REQUIREMENTS_CPU, *VECTOR_REQUIREMENTS],
        "artifacts": CPU_ALL,
        "source_profiles": CPU_PROFILE_SOURCES,
        "cleanup_patterns": ALL_CLEANUP,
        "required_modules": ALL_REQUIRED,
        "validation_probes": ALL_REQUIRED,
    },
    ("cuda", "semantic-core"): {
        "requirements": SEMANTIC_REQUIREMENTS_CUDA,
        "artifacts": CUDA_SEMANTIC,
        "source_profiles": CUDA_PROFILE_SOURCES,
        "cleanup_patterns": SEMANTIC_CLEANUP,
        "required_modules": SEMANTIC_REQUIRED,
        "validation_probes": SEMANTIC_REQUIRED,
    },
    ("cuda", "compute-core"): {
        "requirements": SEMANTIC_REQUIREMENTS_CUDA[:3],
        "artifacts": CUDA_SEMANTIC,
        "source_profiles": CUDA_PROFILE_SOURCES,
        "cleanup_patterns": SEMANTIC_CLEANUP,
        "required_modules": ["torch", "numpy", "scipy"],
        "validation_probes": ["torch", "numpy", "scipy"],
    },
    ("cuda", "model-stack"): {
        "requirements": SEMANTIC_REQUIREMENTS_CUDA[3:],
        "artifacts": CUDA_SEMANTIC,
        "source_profiles": CUDA_PROFILE_SOURCES,
        "cleanup_patterns": SEMANTIC_CLEANUP,
        "required_modules": ["sentence_transformers", "transformers", "huggingface_hub", "safetensors"],
        "validation_probes": ["sentence_transformers", "transformers", "huggingface_hub", "safetensors"],
    },
    ("cuda", "vector-store"): {
        "requirements": VECTOR_REQUIREMENTS,
        "artifacts": CUDA_VECTOR,
        "source_profiles": VECTOR_SOURCES,
        "cleanup_patterns": VECTOR_CLEANUP,
        "required_modules": VECTOR_REQUIRED,
        "validation_probes": VECTOR_REQUIRED,
    },
    ("cuda", "all"): {
        "requirements": [*SEMANTIC_REQUIREMENTS_CUDA, *VECTOR_REQUIREMENTS],
        "artifacts": CUDA_ALL,
        "source_profiles": CUDA_PROFILE_SOURCES,
        "cleanup_patterns": ALL_CLEANUP,
        "required_modules": ALL_REQUIRED,
        "validation_probes": ALL_REQUIRED,
    },
}


def materialize_artifacts(keys: list[str]) -> list[dict[str, str]]:
    return [dict(ARTIFACTS[key]) for key in keys]


def write_manifest(profile: str, component: str, payload: dict[str, object]) -> None:
    manifest_path = MANIFESTS_DIR / profile / f"{component}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 2,
        "profile": profile,
        "component": component,
        "python_tag": "cp313",
        "platform_tag": "win_amd64",
        "requirements": payload["requirements"],
        "artifacts": materialize_artifacts(payload["artifacts"]),
        "source_profiles": payload["source_profiles"],
        "cleanup_patterns": payload["cleanup_patterns"],
        "required_modules": payload["required_modules"],
        "validation_probes": payload["validation_probes"],
    }
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    for (profile, component), payload in sorted(MANIFESTS.items()):
        write_manifest(profile, component, payload)


if __name__ == "__main__":
    main()
