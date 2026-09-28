# Reference fits of designs whose likelihood's recession cone is not its lineality, or whose
# columns depend on one another (SPEC §9.5; D360, D361), from several starts.
#
# Run with the survival package, from this directory:  Rscript cone.R > cone.json
# The checked-in cone.json was written by R 4.3.3 with survival 3.5-8.
#
# Each case is a design (per unit a stratum, a time, a status, an entry or none, its covariates)
# and coxph's fit of it (ties = "efron", strata(stratum), timefix = FALSE, iter.max = 100) from
# each start in `starts`: its coefficients, null where coxph reports NA (a column it drops, at
# the start or later in the iteration), and its last log-likelihood, or null where coxph stops
# ("exp overflow"). A coefficient the cone finds finite converges to the
# same value from every start; one it finds infinite runs off in its sign or is dropped; one it
# finds not identified may end anywhere. The designs are the plan reviews' of #61 and #62: two
# groups separated by a column with a third estimated (e1, e2, the latter with ties and delayed
# entry), strata each separating a different column (e3), a contrast identified and none of its
# columns (b1), exact and within-stratum dependencies (b4), two cohorts apart from the reference
# (b5), a column monotone in one stratum's times (b6, and with two events tied), a separated
# combination of two columns (combo), a cohort whose one event has another at risk (apart), an
# exact sum with an independent column (dep), and a large column whose exact partner R drops
# first (order). Where R's fit of the finite part alone is simple, `finite` holds it (the
# stratum that identifies the estimated column), null for the columns it does not fit, and
# `hazards` holds cox.zph's test (transform = "km") of the estimated columns in the finite part,
# its classes the strata and its other columns fitted beside them, the transform pooled over
# every unit; the last designs are a separated contrast of two columns (a − b) beside a column
# the finite part fits and does not report (a), with an estimated c, and a design R fits whole
# (a column constant within strata, which R drops, and a stratum whose units share their
# covariates), where the test is R's global one.

suppressPackageStartupMessages(library(survival))

num <- function(x) if (length(x) == 0 || is.na(x) || !is.finite(x)) "null" else sprintf("%.17g", x)
vec <- function(x) paste0("[", paste(vapply(x, num, ""), collapse = ", "), "]")
rows <- function(m) paste0("[", paste(apply(m, 1, vec), collapse = ", "), "]")

cases <- list()
add <- function(name, d, columns, entries = FALSE, finite = NULL, hazards = NULL) {
  cases[[length(cases) + 1]] <<- list(name = name, d = d, columns = columns, entries = entries,
                                      finite = finite, hazards = hazards)
}
zph <- function(fit, term) cox.zph(fit, transform = "km")$table[term, "chisq"]

set.seed(7)
n <- 300
x1 <- rbinom(n, 1, .5); x2 <- rbinom(n, 1, .5); s <- x1 + x2
x3 <- round(rnorm(n) + 1.5 * x1, 3)
t0 <- rexp(n, exp(0.8 * (x1 - x2) + 0.7 * x3)); t0 <- t0 / (max(t0) + 1)
add("e1", data.frame(g = 1, t = round((2 - s) + t0, 6), s = rbinom(n, 1, .8), x1, x2, x3),
    c("x1", "x2", "x3"))

set.seed(11)
n <- 400
x1 <- rbinom(n, 1, .5); x2 <- rbinom(n, 1, .5); s <- x1 + x2
x3 <- round(rnorm(n) + 1.5 * x1, 3); x4 <- sample(0:3, n, TRUE)
t0 <- rexp(n, exp(0.8 * (x1 - x2) + 0.7 * x3 - 0.3 * x4)); t0 <- round(10 * t0 / (max(t0) + 1)) / 10
time <- 2 * (2 - s) + t0 + 0.05
add("e2", data.frame(g = 1, e = round(pmax(0, time - runif(n, 0.02, 2.5)), 3), t = time,
                     s = rbinom(n, 1, .8), x1, x2, x3, x4), c("x1", "x2", "x3", "x4"), TRUE)

set.seed(3)
mk <- function(g, x1, x2, n, t, s) data.frame(g = g, t = t, s = s, x1 = x1, x2 = x2,
                                              x3 = round(rnorm(n), 3))
n <- 80; x3 <- round(rnorm(n), 3)
add("e3", rbind(mk(1, 1, 1, 3, 1:3, 1), mk(1, 0, 0, 4, 5:8, 0), mk(2, 1, -1, 5, 1:5, 1),
                mk(2, 0, 0, 4, 7:10, 0),
                data.frame(g = 3, t = round(rexp(n, exp(.6 * x3)), 4), s = rbinom(n, 1, .8),
                           x1 = 0, x2 = 0, x3 = x3)), c("x1", "x2", "x3"))

set.seed(2)
d <- data.frame(g = c(1, 1, 2, 2), t = c(1, 2, 1, 2), s = c(1, 0, 1, 0), x1 = c(1, 0, 0, 0),
                x2 = c(0, 0, 1, 0), x3 = 0)
n <- 60; z <- round(rnorm(n), 3)
add("b1", rbind(d, data.frame(g = 3, t = round(rexp(n, exp(0.7 * z)), 4), s = rbinom(n, 1, .8),
                              x1 = z, x2 = -z, x3 = z)), c("x1", "x2", "x3"))

set.seed(21)
mkd <- function(n = 120) {
  e <- round(runif(n, 0, 3), 1)
  data.frame(g = sample(1:3, n, TRUE), e = e, t = round(e + round(rexp(n, .5), 1) + .1, 1),
             s = rbinom(n, 1, .7))
}
# (times rounded to the tenth they are drawn at, so that no two differ by an ulp, which
# agreg.fit's shift of each stratum's times would round together or apart: D359)
d <- mkd(); d$a <- round(rnorm(nrow(d)), 3); d$b <- d$a; d$c <- rpois(nrow(d), 2)
add("b4 a repeated column", d, c("a", "b", "c"), TRUE)
d <- mkd(); d$a <- sample(0:4, nrow(d), TRUE); d$b <- sample(0:4, nrow(d), TRUE); d$c <- d$a + 2 * d$b
add("b4 a column that sums two", d, c("a", "b", "c"), TRUE)
d <- mkd(); a <- sample(0:4, nrow(d), TRUE); b <- sample(0:4, nrow(d), TRUE)
d$a <- a + 2 * b; d$b <- a; d$c <- b
add("b4 the sum first", d, c("a", "b", "c"), TRUE)
d <- mkd(); d$a <- round(rnorm(nrow(d)), 3); d$b <- c(1.5, -2, 7)[d$g]; d$c <- round(rnorm(nrow(d)), 3)
add("b4 a column constant within strata", d, c("a", "b", "c"), TRUE)

set.seed(5)
g <- rep(c("A", "B", "C"), c(40, 40, 40)); e <- ifelse(g == "A", 0, 20)
add("b5", data.frame(g = 1, e = e,
                     t = round(e + runif(120, 0.1, 10) * ifelse(g == "C", 0.6, 1), 4),
                     s = rbinom(120, 1, .8), B = as.numeric(g == "B"), C = as.numeric(g == "C")),
    c("B", "C"), TRUE)

set.seed(8)
n1 <- 30; x <- round(sort(rnorm(n1), decreasing = TRUE), 3)
s1 <- data.frame(g = 1, t = 1:n1, s = rbinom(n1, 1, .7), c = rbinom(n1, 1, .5), x = x)
n2 <- 80; c2 <- rbinom(n2, 1, .5)
s2 <- data.frame(g = 2, t = round(rexp(n2, exp(.8 * c2)), 4), s = rbinom(n2, 1, .8), c = c2,
                 x = 0.3)
d <- rbind(s1, s2)
alone <- coxph(Surv(t, s) ~ c, subset(d, g == 2), ties = "efron")
d$k <- ifelse(d$g == 2, 0, seq_len(nrow(d)))
part <- coxph(Surv(t, s) ~ c + strata(k), d, ties = "efron")
add("b6", d[, c("g", "t", "s", "c", "x")], c("c", "x"), finite = c(unname(coef(alone)), NA),
    hazards = zph(part, "c"))
d$t[d$g == 1][2] <- 1; d$s[d$g == 1][1:2] <- 1
add("b6, two events tied", d, c("c", "x"))

set.seed(2)
n <- 200; t <- round(rexp(n), 4); x1 <- round(rnorm(n), 3)
add("combo", data.frame(g = 1, t = t, s = rbinom(n, 1, .8), x0 = -t - x1, x1 = x1,
                        x2 = round(rnorm(n), 3)), c("x0", "x1", "x2"))

add("apart", data.frame(g = 1, e = c(2.5, 0, 0, 0), t = c(3, 1, 2, 1), s = c(1, 0, 1, 1),
                        B = c(0, 0, 1, 0), E = c(0, 0, 0, 1)), c("B", "E"), TRUE)

set.seed(8)
n <- 150; a <- sample(-3:3, n, TRUE); b <- sample(-3:3, n, TRUE)
add("dep", data.frame(g = 1, t = sample(1:40, n, TRUE), s = rbinom(n, 1, .7), x1 = a, x2 = b,
                      x3 = a + b, x4 = round(a + rnorm(n), 3)), c("x1", "x2", "x3", "x4"))

set.seed(7)
n <- 300; x1 <- sample(0:1e8, n, TRUE); x2 <- sample(0:5, n, TRUE); x4 <- round(rnorm(n), 3)
add("order", data.frame(g = 1, t = round(rexp(n, exp(0.5 * x2 + 0.3 * x4)), 5),
                        s = rbinom(n, 1, .8), x4 = x4, x1 = x1, x3 = x1 + x2, x2 = x2),
    c("x4", "x1", "x3", "x2"))

set.seed(4)
n <- 80; a <- round(rnorm(n), 3); x3 <- round(rnorm(n), 3)
d <- rbind(
  data.frame(g = 1, t = round(rexp(n, exp(0.5 * a + 0.4 * x3)) * 30 + 10, 2),
             s = rbinom(n, 1, .8), a = a, b = a, c = x3),
  data.frame(g = 1, t = c(1, 2, 3, 4, 5, 6, 9.5, 9.5, 9.5), s = c(1, 1, 0, 1, 1, 1, 0, 0, 0),
             a = c(1, 1, 0, 0, 0, 0, 0, 0, 0), b = c(0, 0, 0, 0, 0, 0, 1, 2, 5), c = 0))
d$k <- d$a - d$b
part <- coxph(Surv(t, s) ~ a + c + strata(k), d, ties = "efron")
add("a separated contrast beside a column fitted in the finite part", d[, 1:6], c("a", "b", "c"),
    finite = c(NA, NA, unname(coef(part)["c"])), hazards = zph(part, "c"))

set.seed(2)
n <- 80; z <- round(rnorm(n), 3)
d <- rbind(data.frame(g = 1, t = round(rexp(n, 1) * 10, 2), s = rbinom(n, 1, .8), z = z,
                      b = rbinom(n, 1, .5), c = 1.5),
           data.frame(g = 2, t = round(rexp(n, 0.3) * 10, 2), s = rbinom(n, 1, .8), z = 0.5,
                      b = 1, c = -2))
whole <- coxph(Surv(t, s) ~ z + b + c + strata(g), d, ties = "efron",
               control = coxph.control(timefix = FALSE))
add("a column constant within strata beside a stratum whose units share their covariates", d,
    c("z", "b", "c"), finite = c(unname(coef(whole))[1:2], NA), hazards = zph(whole, "GLOBAL"))

entries <- character()
for (c in cases) {
  d <- c$d
  m <- length(c$columns)
  surv <- if (c$entries) "Surv(e, t, s)" else "Surv(t, s)"
  formula <- as.formula(paste(surv, "~", paste(c$columns, collapse = " + "), "+ strata(g)"))
  fits <- character()
  logliks <- character()
  starts <- list(rep(0, m), rep(3, m), rep(-3, m))
  for (start in starts) {
    fit <- tryCatch(
      suppressWarnings(coxph(formula, data = d, ties = "efron", init = start,
                             control = coxph.control(timefix = FALSE, iter.max = 100))),
      error = function(e) NULL)
    fits <- c(fits, if (is.null(fit)) "null" else vec(unname(fit$coefficients)))
    logliks <- c(logliks, if (is.null(fit)) "null" else num(fit$loglik[2]))
  }
  entries <- c(entries, sprintf(
    "{\"name\": \"%s\", \"stratum\": %s, \"entry\": %s, \"time\": %s, \"status\": %s, \"x\": %s, \"starts\": %s, \"fits\": [%s], \"loglik\": [%s], \"finite\": %s, \"hazards\": %s}",
    c$name, vec(d$g), if (c$entries) vec(d$e) else "null", vec(d$t), vec(d$s),
    rows(as.matrix(d[, c$columns])), rows(do.call(rbind, starts)), paste(fits, collapse = ", "),
    paste(logliks, collapse = ", "), if (is.null(c$finite)) "null" else vec(c$finite),
    if (is.null(c$hazards)) "null" else num(c$hazards)))
}
cat(sprintf("{\"r\": \"%s\", \"survival\": \"%s\",\n\"cases\": [%s]}\n", R.version.string,
            as.character(packageVersion("survival")), paste(entries, collapse = ",\n  ")))
