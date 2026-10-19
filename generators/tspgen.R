# Generate one tspgen instance (Bossek et al., FOGA 2019) as TSPLIB.
#
# Usage: Rscript tspgen.R n seed iters upper output
#
# Runs the iterative generator (Alg. 1 of the paper) with the
# "sophisticated" mutation operators and the package's default
# out-of-bounds handling (re-placement uniformly at random), then scales
# the [0, 1]^2 points to [0, upper]^2 and writes EUC_2D TSPLIB with six
# decimals.  The R, tspgen and netgen versions go into the COMMENT line,
# because R's random stream may change between R versions.

arguments <- commandArgs(trailingOnly = TRUE)
if (length(arguments) != 5L) {
  stop("usage: Rscript tspgen.R n seed iters upper output")
}
n <- as.integer(arguments[[1L]])
seed <- as.integer(arguments[[2L]])
iters <- as.integer(arguments[[3L]])
upper <- as.numeric(arguments[[4L]])
output <- arguments[[5L]]

suppressPackageStartupMessages(library(tspgen))

set.seed(seed)
network <- tspgen::build(
  n = n, iters = iters, collection = tspgen::init("sophisticated"), upper = upper
)
comment <- sprintf(
  "tspgen %s (netgen %s, %s) preset=sophisticated n=%d seed=%d iters=%d upper=%g",
  as.character(packageVersion("tspgen")),
  as.character(packageVersion("netgen")),
  R.version.string, n, seed, iters, upper
)
netgen::exportToTSPlibFormat(
  network, output,
  name = sprintf("tspgen-%d-%d", n, seed), comment = comment,
  use.extended.format = FALSE, digits = 6L
)
