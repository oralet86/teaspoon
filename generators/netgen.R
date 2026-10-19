# Generate one clustered netgen instance as TSPLIB.
#
# Usage: Rscript netgen.R n seed clusters scale output
#
# Calls netgen::generateClusteredNetwork with the package defaults, which
# are the settings of the NETGEN set (Kerschke et al., Evol. Comput. 2018,
# Sec. 3.3): cluster centres from a maximin Latin hypercube sample on
# [0, 100]^2, an equal number of cities per cluster, each cluster a normal
# distribution with the distance to the nearest other centre as variance,
# out-of-bounds points mirrored back.  The variance is not scale-free, so
# the instance is generated on [0, 100]^2 and only then multiplied by
# `scale`.  EUC_2D TSPLIB with six decimals; versions go into COMMENT.

arguments <- commandArgs(trailingOnly = TRUE)
if (length(arguments) != 5L) {
  stop("usage: Rscript netgen.R n seed clusters scale output")
}
n <- as.integer(arguments[[1L]])
seed <- as.integer(arguments[[2L]])
clusters <- as.integer(arguments[[3L]])
scale <- as.numeric(arguments[[4L]])
output <- arguments[[5L]]

suppressPackageStartupMessages(library(netgen))

set.seed(seed)
network <- netgen::generateClusteredNetwork(n.cluster = clusters, n.points = n)
network$coordinates <- network$coordinates * scale
network$lower <- network$lower * scale
network$upper <- network$upper * scale
comment <- sprintf(
  "netgen %s (%s) generateClusteredNetwork n=%d seed=%d clusters=%d scale=%g",
  as.character(packageVersion("netgen")), R.version.string,
  n, seed, clusters, scale
)
netgen::exportToTSPlibFormat(
  network, output,
  name = sprintf("netgen-%d-%d", n, seed), comment = comment,
  use.extended.format = FALSE, digits = 6L
)
