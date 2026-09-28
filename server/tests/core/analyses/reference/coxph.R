# Reference outputs for the Cox model of a design and its test of proportional hazards
# (SPEC §9.5; D358, D359).
#
# Run with the survival package, from this directory:  Rscript coxph.R > coxph.json
# (coxph-far.csv holds the design of "a unit far from the others", one on which agfit4 recentres
# its linear predictor and converges).
# The checked-in coxph.json was written by R 4.3.3 with survival 3.5-8.
#
# Each case is a design: per unit a stratum, a time, a status, an entry (or none) and its
# covariates; and coxph's fit of it (ties = "efron", strata(stratum), timefix = FALSE, so that
# equal times stay equal and unequal ones apart): its coefficients (null where coxph reports NA,
# a column it drops as dependent), standard errors, log-likelihoods, iterations, whether it
# converged (no warning), and cox.zph's global test (transform = "km"), null where it fails.
# Designs mix numbers, 0/1 and -1/0/1 columns, integers, a column that repeats another, one that
# is constant, one that sums two others, and columns of scale 1e6 and 1e-6, with and without
# entries, over one to three strata, with ties; and a unit far from the others (agfit4's centre,
# and its recentring), a near dependence, a risk set that empties, and a unit whose follow-up
# spans no event time (which agreg.fit ignores and zph2 counts at risk, "ghost"), with entries;
# a sentinel value far from the others without entries (coxfit6 does not recentre), and strata
# far apart with entries, on which, as on the far unit, cox.zph stops ("exp overflow"); and every
# event at one time, on which cox.zph fails (its transform of time has no spread).

suppressPackageStartupMessages(library(survival))

num <- function(x) if (length(x) == 0 || is.na(x) || !is.finite(x)) "null" else sprintf("%.17g", x)
vec <- function(x) paste0("[", paste(vapply(x, num, ""), collapse = ", "), "]")
obj <- function(...) {
  parts <- list(...)
  paste0("{", paste(sprintf("\"%s\": %s", names(parts), unlist(parts)), collapse = ", "), "}")
}
rows <- function(m) paste0("[", paste(apply(m, 1, vec), collapse = ", "), "]")

column <- function(kind, n, before) {
  switch(kind,
    number = round(rnorm(n, 0, 2), 3),
    binary = as.numeric(runif(n) < 0.4),
    signed = sample(c(-1, 0, 1), n, replace = TRUE),
    integer = as.numeric(sample(0:9, n, replace = TRUE)),
    repeated = before[, ncol(before)],
    constant = rep(5, n),
    sum = before[, ncol(before)] + before[, ncol(before) - 1],
    large = round(rnorm(n), 3) * 1e6,
    small = round(rnorm(n), 3) * 1e-6)
}

design <- function(name, n, kinds, strata, entries) {
  x <- matrix(numeric(), n, 0)
  for (kind in kinds) x <- cbind(x, column(kind, n, x))
  time <- as.numeric(sample(1:30, n, replace = TRUE))
  entry <- if (entries) as.numeric(sapply(time, function(t) sample(0:(t - 1), 1))) else NULL
  list(name = name, stratum = sample(seq_len(strata), n, replace = TRUE), time = time,
       status = as.numeric(runif(n) < 0.7), entry = entry, x = x, kinds = kinds)
}

set.seed(20260928)
kinds <- c("number", "binary", "signed", "integer", "large", "small")
cases <- list(
  design("numbers and 0/1", 80, c("number", "binary"), 1, FALSE),
  design("numbers and 0/1, with entries", 80, c("number", "binary"), 1, TRUE),
  design("three strata", 120, c("number", "signed", "integer"), 3, FALSE),
  design("three strata, with entries", 120, c("number", "signed", "integer"), 3, TRUE),
  design("a repeated column", 60, c("number", "repeated"), 1, FALSE),
  design("a repeated column, with entries", 60, c("number", "repeated"), 2, TRUE),
  design("a constant column", 60, c("binary", "constant", "number"), 1, FALSE),
  design("a column that sums two", 70, c("number", "signed", "sum"), 2, FALSE),
  design("a column that sums two, with entries", 70, c("number", "signed", "sum"), 1, TRUE),
  design("columns of scale 1e6 and 1e-6", 90, c("large", "small", "binary"), 2, FALSE),
  design("columns of scale 1e6 and 1e-6, with entries", 90, c("large", "small", "binary"), 2, TRUE),
  design("one column", 40, c("number"), 1, FALSE)
)
special <- function(name, stratum, time, status, entry, x, kinds) {
  list(name = name, stratum = stratum, time = time, status = status, entry = entry,
       x = as.matrix(x), kinds = kinds)
}
far <- read.csv("coxph-far.csv")
cases[[length(cases) + 1]] <- special(
  "a unit far from the others, with entries", far$g, far$t, far$s, far$e, cbind(far$x1), "far")
near <- cbind(round(rnorm(120), 3), round(rnorm(120), 3))
near <- cbind(near, near[, 1] + near[, 2] + 1e-4 * round(rnorm(120), 3))
nt <- as.numeric(sample(2:30, 120, replace = TRUE))
cases[[length(cases) + 1]] <- special(
  "a near dependence, with entries", rep(1, 120), nt, as.numeric(runif(120) < 0.7),
  as.numeric(sapply(nt, function(t) sample(0:(t - 1), 1))), near, c("near", "near", "near"))
blocks <- function(first, second, entry, cap, ghost) {
  xa <- round(rnorm(60), 3)
  ta <- pmin(cap[1], pmax(1, round(rexp(60, exp(xa)) * first)))
  xb <- round(30 + rnorm(60), 3)
  tb <- pmin(cap[3], pmax(cap[2], second + round(rexp(60, exp(xb - 30)) * first)))
  time <- c(ta, tb); x <- c(xa, xb); entries <- c(rep(0, 60), rep(entry, 60))
  if (ghost) { time <- c(time, 6.8); x <- c(x, 0.1); entries <- c(entries, 6.5) }
  list(time = as.numeric(time), x = cbind(x), entry = as.numeric(entries),
       status = c(as.numeric(runif(120) < 0.8), if (ghost) 0))
}
empties <- blocks(3, 10, 9, c(8, 11, 20), FALSE)
cases[[length(cases) + 1]] <- special(
  "a risk set that empties", rep(1, 120), empties$time, empties$status, empties$entry,
  empties$x, "number")
ghost <- blocks(2, 4, 4, c(3, 5, 7), TRUE)
cases[[length(cases) + 1]] <- special(
  "a unit whose follow-up spans no event time", rep(1, 121), ghost$time, ghost$status,
  ghost$entry, ghost$x, "ghost")
sx <- c(-1.2, -0.8, -0.5, -0.3, -0.1, 0, 0.2, 0.4, 0.7, 1.1, 1.5, -1.6)
st <- c(9, 7.5, 8, 5, 6.5, 3, 4, 2.5, 1, 2, 0.5, 6)
cases[[length(cases) + 1]] <- special(
  "a sentinel value", rep(1, 13), c(st, 20), c(rep(1, 12), 0), NULL, cbind(c(sx, -999)),
  "number")
cases[[length(cases) + 1]] <- special(
  "strata far apart, with entries", rep(1:2, each = 12), c(st, st), rep(1, 24), rep(0, 24),
  cbind(c(sx, sx - 999)), "far")
twice <- design("every unit twice", 50, c("number", "binary"), 2, TRUE)
for (part in c("stratum", "time", "status", "entry")) twice[[part]] <- rep(twice[[part]], 2)
twice$x <- rbind(twice$x, twice$x)
cases[[length(cases) + 1]] <- twice
for (index in 1:16) {
  count <- 1 + (index - 1) %% 4
  cases[[length(cases) + 1]] <- design(
    sprintf("random %d", index), sample(30:200, 1), sample(kinds, count, replace = TRUE),
    1 + (index - 1) %% 3, index > 8)
}

ox <- round(rnorm(40), 3)
cases[[length(cases) + 1]] <- special(
  "every event at one time", rep(1, 40), c(rep(3, 24), rep(c(3, 8, 12, 20), 4)),
  c(rep(1, 24), rep(0, 16)), NULL, cbind(ox), "number")

entries <- character()
for (c in cases) {
  d <- data.frame(t = c$time, s = c$status, g = c$stratum)
  names <- sprintf("x%d", seq_len(ncol(c$x)))
  for (j in seq_len(ncol(c$x))) d[[names[j]]] <- c$x[, j]
  d$e <- if (is.null(c$entry)) 0 else c$entry
  surv <- if (is.null(c$entry)) "Surv(t, s)" else "Surv(e, t, s)"
  formula <- as.formula(paste(surv, "~", paste(names, collapse = " + "), "+ strata(g)"))
  converged <- TRUE
  fit <- withCallingHandlers(
    coxph(formula, data = d, ties = "efron", control = coxph.control(timefix = FALSE)),
    warning = function(w) {
      converged <<- FALSE
      invokeRestart("muffleWarning")
    })
  table <- tryCatch(suppressWarnings(cox.zph(fit, transform = "km"))$table,
                    error = function(e) NULL)
  zph <- if (is.null(table)) "null" else
    obj(statistic = num(table[nrow(table), "chisq"]), df = num(table[nrow(table), "df"]),
        p = num(table[nrow(table), "p"]))
  se <- rep(NA, ncol(c$x))
  se[!is.na(fit$coefficients)] <- sqrt(diag(fit$var))[!is.na(fit$coefficients)]
  entries <- c(entries, obj(
    name = sprintf("\"%s\"", c$name),
    stratum = vec(c$stratum), time = vec(c$time), status = vec(c$status),
    entry = if (is.null(c$entry)) "null" else vec(c$entry), x = rows(c$x),
    kinds = paste0("[", paste(sprintf("\"%s\"", c$kinds), collapse = ", "), "]"),
    coef = vec(unname(fit$coefficients)), se = vec(se), var = rows(as.matrix(fit$var)),
    loglik = vec(fit$loglik),
    iter = num(fit$iter), converged = if (converged) "true" else "false", zph = zph
  ))
}

cat(sprintf("{\"r\": \"%s\", \"survival\": \"%s\",\n\"cases\": [%s]}\n", R.version.string,
            as.character(packageVersion("survival")), paste(entries, collapse = ",\n  ")))
