/* Test-process resolver allowlist. Git recovery separately disables transports. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <netdb.h>
#include <stdlib.h>
#include <string.h>

int getaddrinfo(const char *node, const char *service,
                const struct addrinfo *hints, struct addrinfo **result) {
    const char *allowed = getenv("GITPICKLE_TEST_STORAGE_HOST");
    if (!node || !allowed || strcmp(node, allowed) != 0)
        return EAI_FAIL;
    int (*original)(const char *, const char *, const struct addrinfo *,
                    struct addrinfo **) = dlsym(RTLD_NEXT, "getaddrinfo");
    return original(node, service, hints, result);
}
