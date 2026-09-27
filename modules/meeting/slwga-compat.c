#include <windows.h>

/* Wine 缺少旧查询入口；如实报告未实现，不伪造授权或查询成功。 */
__declspec(dllexport) HRESULT WINAPI
SLIsGenuineLocal(const GUID *application, int *state, void *options)
{
    (void)options;
    if (!application || !state)
        return E_INVALIDARG;
    *state = 4; /* SL_GEN_STATE_LAST：无有效结果。 */
    return E_NOTIMPL;
}
