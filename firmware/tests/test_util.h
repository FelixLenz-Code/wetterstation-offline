/* Winziger Testrahmen -- kein Framework noetig fuer reine Rechenlogik. */
#ifndef TEST_UTIL_H
#define TEST_UTIL_H

#include <math.h>
#include <stdio.h>
#include <string.h>

static int tests_run = 0;
static int tests_failed = 0;
static const char *current_test = "";

#define TEST(name)                                                  \
    static void name(void);                                         \
    static void run_##name(void)                                    \
    {                                                               \
        current_test = #name;                                       \
        tests_run++;                                                \
        name();                                                     \
    }                                                               \
    static void name(void)

#define RUN(name) run_##name()

#define CHECK(cond)                                                            \
    do {                                                                       \
        if (!(cond)) {                                                         \
            printf("  FEHLER %s:%d in %s: %s\n", __FILE__, __LINE__,           \
                   current_test, #cond);                                       \
            tests_failed++;                                                    \
            return;                                                            \
        }                                                                      \
    } while (0)

#define CHECK_NEAR(a, b, tol)                                                  \
    do {                                                                       \
        double _a = (double)(a), _b = (double)(b);                             \
        if (!(fabs(_a - _b) <= (tol))) {                                       \
            printf("  FEHLER %s:%d in %s: %s (%g) != %s (%g), Toleranz %g\n",  \
                   __FILE__, __LINE__, current_test, #a, _a, #b, _b,           \
                   (double)(tol));                                             \
            tests_failed++;                                                    \
            return;                                                            \
        }                                                                      \
    } while (0)

static inline int test_summary(const char *suite)
{
    if (tests_failed == 0) {
        printf("%s: %d Tests bestanden\n", suite, tests_run);
        return 0;
    }
    printf("%s: %d von %d Tests fehlgeschlagen\n", suite, tests_failed, tests_run);
    return 1;
}

#endif /* TEST_UTIL_H */
