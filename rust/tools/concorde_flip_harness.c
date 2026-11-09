/* Applies flips with Concorde's two-level-list flipper (flip_two.c) and
 * prints next[] for every city after each flip.  Input: n m, then m lines
 * "x y" meaning CClinkern_flipper_flip(F, x, y) on the identity cycle. */
#include <stdio.h>
#include <stdlib.h>
#include "machdefs.h"
#include "util.h"
#include "linkern.h"

int main (void)
{
    int n, m, k, c, x, y;
    CClk_flipper F;
    int *cyc;
    if (scanf ("%d %d", &n, &m) != 2) return 1;
    cyc = malloc (n * sizeof (int));
    for (c = 0; c < n; c++) cyc[c] = c;
    if (CClinkern_flipper_init (&F, n, cyc)) return 1;
    for (k = 0; k < m; k++) {
        if (scanf ("%d %d", &x, &y) != 2) return 1;
        CClinkern_flipper_flip (&F, x, y);
        for (c = 0; c < n; c++) printf ("%d ", CClinkern_flipper_next (&F, c));
        printf ("\n");
    }
    CClinkern_flipper_finish (&F);
    free (cyc);
    return 0;
}
