/*************************************************************************
 * Copyright (c) 2015-2018, NVIDIA CORPORATION. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 ************************************************************************/


// Dynamically handle dependencies on external libraries (other than cudart).

#ifndef DALI_UTIL_NVML_WRAP_H_
#define DALI_UTIL_NVML_WRAP_H_
#include <nvml.h>
#include <cuda_runtime_api.h>

#include "dali/core/common.h"
#include "dali/core/error_handling.h"

namespace dali {

namespace nvml {

DLL_PUBLIC bool wrapIsInitialized(void);
DLL_PUBLIC DALIError_t wrapSymbols(void);

DLL_PUBLIC DALIError_t wrapNvmlInit(void);
DLL_PUBLIC DALIError_t wrapNvmlShutdown(void);
DLL_PUBLIC DALIError_t wrapNvmlDeviceGetHandleByPciBusId(const char* pciBusId,
                                                         nvmlDevice_t* device);
DLL_PUBLIC DALIError_t wrapNvmlDeviceGetHandleByIndex(const int device_id,
                                                      nvmlDevice_t* device);
DLL_PUBLIC DALIError_t wrapNvmlDeviceGetIndex(nvmlDevice_t device, unsigned* index);
DLL_PUBLIC DALIError_t wrapNvmlDeviceSetCpuAffinity(nvmlDevice_t device);
DLL_PUBLIC DALIError_t wrapNvmlSystemGetDriverVersion(char* name, unsigned int length);
DLL_PUBLIC DALIError_t wrapNvmlDeviceGetCpuAffinity(nvmlDevice_t device,
                                                    unsigned int cpuSetSize,
                                                    unsigned long* cpuSet);  // NOLINT(runtime/int)
DLL_PUBLIC DALIError_t wrapNvmlDeviceClearCpuAffinity(nvmlDevice_t device);

#if (CUDART_VERSION >= 11000)

DLL_PUBLIC DALIError_t wrapNvmlDeviceGetCpuAffinityWithinScope(nvmlDevice_t device,
                                                               unsigned int nodeSetSize,
                                                               unsigned long *nodeSet,  // NOLINT(*)
                                                               nvmlAffinityScope_t scope);
DLL_PUBLIC DALIError_t wrapNvmlDeviceGetBrand(nvmlDevice_t device, nvmlBrandType_t* type);
DLL_PUBLIC DALIError_t wrapNvmlDeviceGetCount_v2(unsigned int* deviceCount);
DLL_PUBLIC DALIError_t wrapNvmlDeviceGetHandleByIndex_v2(unsigned int index, nvmlDevice_t* device);
DLL_PUBLIC DALIError_t wrapNvmlDeviceGetCudaComputeCapability(nvmlDevice_t device,
                                                              int* major, int* minor);

#endif

/**
 * Checks, whether CUDA11-proper NVML functions have been successfully loaded
 */
DLL_PUBLIC bool wrapHasCuda11NvmlFunctions();

}  // namespace nvml

}  // namespace dali

#endif  // DALI_UTIL_NVML_WRAP_H_

