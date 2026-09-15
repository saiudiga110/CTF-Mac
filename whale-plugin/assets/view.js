CTFd._internal.challenge.data = undefined;
CTFd._internal.challenge.renderer = null;
CTFd._internal.challenge.preRender = function () { };
CTFd._internal.challenge.render = null;

CTFd._internal.challenge.postRender = function () {
    loadInfo();

    // Explicit submit handler — ensures Correct/Incorrect feedback is shown
    var $submitBtn = $('#challenge-submit');
    var $input = $('#challenge-input');

    $submitBtn.off('click').on('click', function (e) {
        e.preventDefault();
        var $btn = $(this);
        $btn.addClass("disabled-button").prop("disabled", true);
        $btn.text("Submitting...");

        CTFd._internal.challenge.submit()
            .then(function (data) {
                renderSubmissionResponse(data);
                $btn.removeClass("disabled-button").prop("disabled", false);
                $btn.text("Submit");
            })
            .catch(function (err) {
                $btn.removeClass("disabled-button").prop("disabled", false);
                $btn.text("Submit");
                showNotification('alert-danger', 'Error submitting flag. Please try again.');
                console.error('Submit error:', err);
            });
    });

    // Enter key support
    $input.off('keyup').on('keyup', function (e) {
        if (e.keyCode === 13 || e.which === 13) {
            $submitBtn.click();
        }
    });
};

if (typeof $ === 'undefined') {
    var $ = CTFd.lib.$;
}

function showNotification(alertClass, message) {
    var $notif = $('#result-notification');
    var $msg = $('#result-message');
    $notif.removeClass('alert-success alert-danger alert-warning alert-info');
    $notif.addClass(alertClass);
    $msg.html(message);
    $notif.slideDown();
    setTimeout(function () { $notif.slideUp(); }, 5000);
}

function renderSubmissionResponse(response) {
    // CTFd API response can be nested in various ways:
    // response = { success: true, data: { status: "correct", message: "..." } }
    // OR response.data = { success: true, data: { status: "correct" } }
    // We need to find the "status" field

    var result = null;

    // Try different nesting levels to find the status
    if (response && response.data && response.data.status) {
        result = response.data;
    } else if (response && response.data && response.data.data && response.data.data.status) {
        result = response.data.data;
    } else if (response && response.status && typeof response.status === 'string') {
        result = response;
    }

    if (!result) {
        console.log('Full response:', JSON.stringify(response));
        showNotification('alert-warning', 'Unexpected response. Check console.');
        return;
    }

    var status = result.status;
    var message = result.message || '';

    if (status === "correct") {
        showNotification('alert-success',
            '<i class="fas fa-check-circle"></i> Correct! ' + message);
        // Clear input
        $('.challenge-input').val('');
        // Update solves count
        var $solves = $('.challenge-solves');
        if ($solves.length) {
            var txt = $solves.text().trim();
            var count = parseInt(txt) || 0;
            $solves.text((count + 1) + ' Solves');
        }
    } else if (status === "incorrect") {
        showNotification('alert-danger',
            '<i class="fas fa-times-circle"></i> Incorrect! ' + message);
    } else if (status === "already_solved") {
        showNotification('alert-info',
            '<i class="fas fa-info-circle"></i> You already solved this challenge.');
    } else if (status === "paused") {
        showNotification('alert-warning',
            '<i class="fas fa-pause-circle"></i> The CTF is paused.');
    } else if (status === "ratelimited") {
        showNotification('alert-warning',
            '<i class="fas fa-clock"></i> Rate limited. Try again later.');
    } else {
        showNotification('alert-warning', 'Response: ' + status + ' ' + message);
    }
}

function loadInfo() {
    var challenge_id = CTFd._internal.challenge.data.id;
    var url = "/plugins/ctfd-whale/container?challenge_id=" + challenge_id;

    CTFd.fetch(url, {
        method: 'GET',
        credentials: 'same-origin',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
    }).then(function (response) {
        return response.json();
    }).then(function (response) {
        if (window.t !== undefined) {
            clearInterval(window.t);
            window.t = undefined;
        }

        if (response.remaining_time === undefined) {
            $('#whale-panel').html(
                '<div class="card whale-card" style="width:100%">' +
                '<div class="card-body text-center">' +
                '<h5 class="card-title" style="color:#00f0ff">⚡ Instance</h5>' +
                '<p class="text-muted">No instance running</p>' +
                '<button type="button" class="btn btn-primary" id="whale-button-boot" ' +
                'onclick="CTFd._internal.challenge.boot()">🚀 Launch Instance</button>' +
                '</div></div>'
            );
        } else {
            var connInfo = '';
            if (response.type === 'http') {
                connInfo = '<p class="card-text"><a href="http://' + response.domain + '" target="_blank" style="color:#00f0ff">' +
                    'http://' + response.domain + '</a></p>';
            } else {
                connInfo = '<p class="card-text"><code style="color:#ff6b6b;font-size:1.1em">' +
                    response.ip + ':' + response.port + '</code></p>';
            }

            $('#whale-panel').html(
                '<div class="card whale-card" style="width:100%">' +
                '<div class="card-body text-center">' +
                '<h5 class="card-title" style="color:#00f0ff">⚡ Instance Running</h5>' +
                '<h6 class="mb-2" id="whale-challenge-count-down" style="color:#ffa657">⏱ ' + response.remaining_time + 's remaining</h6>' +
                connInfo +
                '<div class="mt-2">' +
                '<button type="button" class="btn btn-sm btn-danger me-2" id="whale-button-destroy" ' +
                'onclick="CTFd._internal.challenge.destroy()">💥 Destroy</button>' +
                '<button type="button" class="btn btn-sm btn-success" id="whale-button-renew" ' +
                'onclick="CTFd._internal.challenge.renew()">🔄 Renew</button>' +
                '</div></div></div>'
            );

            function showAuto() {
                var c = document.getElementById('whale-challenge-count-down');
                if (!c) return;
                var text = c.innerHTML;
                var match = text.match(/(\d+)s/);
                if (match) {
                    var second = parseInt(match[1]) - 1;
                    c.innerHTML = '⏱ ' + second + 's remaining';
                    if (second < 0) { loadInfo(); }
                }
            }
            window.t = setInterval(showAuto, 1000);
        }
    }).catch(function (err) {
        console.error('loadInfo error:', err);
    });
}

CTFd._internal.challenge.destroy = function () {
    var challenge_id = CTFd._internal.challenge.data.id;
    var url = "/plugins/ctfd-whale/container?challenge_id=" + challenge_id;
    var btn = document.getElementById('whale-button-destroy');
    if (btn) { btn.innerHTML = "Destroying..."; btn.disabled = true; }

    CTFd.fetch(url, {
        method: 'DELETE', credentials: 'same-origin',
        headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({})
    }).then(function (r) { return r.json() }).then(function (r) {
        if (r.success) {
            loadInfo();
            showNotification('alert-success', '💥 Instance destroyed!');
        } else {
            if (btn) { btn.innerHTML = "💥 Destroy"; btn.disabled = false; }
            showNotification('alert-danger', r.msg || 'Failed to destroy instance');
        }
    });
};

CTFd._internal.challenge.renew = function () {
    var challenge_id = CTFd._internal.challenge.data.id;
    var url = "/plugins/ctfd-whale/container?challenge_id=" + challenge_id;
    var btn = document.getElementById('whale-button-renew');
    if (btn) { btn.innerHTML = "Renewing..."; btn.disabled = true; }

    CTFd.fetch(url, {
        method: 'PATCH', credentials: 'same-origin',
        headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({})
    }).then(function (r) { return r.json() }).then(function (r) {
        if (r.success) {
            loadInfo();
            showNotification('alert-success', '🔄 Instance renewed!');
        } else {
            if (btn) { btn.innerHTML = "🔄 Renew"; btn.disabled = false; }
            showNotification('alert-danger', r.msg || 'Failed to renew instance');
        }
    });
};

CTFd._internal.challenge.boot = function () {
    var challenge_id = CTFd._internal.challenge.data.id;
    var url = "/plugins/ctfd-whale/container?challenge_id=" + challenge_id;
    var btn = document.getElementById('whale-button-boot');
    if (btn) { btn.innerHTML = "Deploying..."; btn.disabled = true; }

    CTFd.fetch(url, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({})
    }).then(function (r) { return r.json() }).then(function (r) {
        if (r.success) {
            loadInfo();
            showNotification('alert-success', '🚀 Instance deployed!');
        } else {
            if (btn) { btn.innerHTML = "🚀 Launch Instance"; btn.disabled = false; }
            showNotification('alert-danger', r.msg || 'Failed to deploy instance');
        }
    });
};

CTFd._internal.challenge.submit = function (preview) {
    var challenge_id = CTFd._internal.challenge.data.id;
    var inputs = $('.challenge-input');
    var submission;

    if (inputs.length > 1) {
        var values = [];
        inputs.each(function () {
            values.push($(this).val());
        });
        submission = JSON.stringify(values);
    } else {
        submission = inputs.val();
    }

    var body = {
        challenge_id: challenge_id,
        submission: submission,
    };
    var params = {};
    if (preview) {
        params["preview"] = true;
    }

    return CTFd.api.post_challenge_attempt(params, body).then(function (response) {
        return response;
    });
};


// --- Whale Sequential Flag Logic ---

function loadFlagProgress() {
    var challenge_id = CTFd._internal.challenge.data.id;
    var $inputs = $('.whale-flag-input');

    // Only proceed if we are in sequential mode
    if ($inputs.length === 0) return;

    CTFd.fetch("/plugins/ctfd-whale/flags/status?challenge_id=" + challenge_id, {
        method: "GET"
    }).then(function (response) {
        return response.json();
    }).then(function (data) {
        if (data.success) {
            var solved_ids = data.solved_ids;
            var num_solved = solved_ids.length;

            $inputs.each(function (index) {
                var $input = $(this);
                var $btn = $('#flag-btn-' + index);
                var $status = $('#flag-status-' + index);

                if (index < num_solved) {
                    // Already Solved
                    $input.val("Correct!").prop('disabled', true).addClass('is-valid');
                    $btn.prop('disabled', true).text('Solved');
                    // Style the input as requested
                    $input.css({ 'background-color': '#d4edda', 'color': '#155724', 'font-weight': 'bold' });
                    $status.html('<span class="text-success"><i class="fas fa-check-circle"></i> Correct</span>').show();

                } else if (index === num_solved) {
                    // Current Flag to Solve
                    $input.prop('disabled', false).removeClass('is-valid is-invalid');
                    $input.val("").css({ 'background-color': '', 'color': '', 'font-weight': '' });
                    $btn.prop('disabled', false).text('Submit');
                    $status.hide();
                } else {
                    // Locked
                    $input.val("").prop('disabled', true);
                    $input.css({ 'background-color': '#e9ecef', 'color': '', 'font-weight': '' });
                    $btn.prop('disabled', true).text('Locked');
                    $status.hide();
                }
            });
        }
    });
}

function submitFlag(index) {
    var challenge_id = CTFd._internal.challenge.data.id;
    var $input = $('#flag-input-' + index);
    var value = $input.val();
    var $btn = $('#flag-btn-' + index);
    var $status = $('#flag-status-' + index);

    $btn.prop('disabled', true).text('Check...');

    CTFd.fetch("/plugins/ctfd-whale/flags/submit", {
        method: "POST",
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            challenge_id: challenge_id,
            submission: value,
            flag_idx: index
        })
    }).then(function (response) {
        return response.json();
    }).then(function (data) {
        if (data.success) {
            // Correct
            $input.addClass('is-valid').removeClass('is-invalid');
            $status.html('<span class="text-success fw-bold">' + data.msg + '</span>').show();

            // Refresh progress to unlock next
            loadFlagProgress();

            // Check if this was the last flag
            var total_flags = $('.whale-flag-input').length;
            // We check if index + 1 == total
            if (index + 1 === total_flags) {
                // Trigger full solve logic
                setTimeout(function () {
                    // We call the internal submit function. The backend 'attempt' checks progress in DB.
                    CTFd._internal.challenge.submit()
                        .then(function (resp) { renderSubmissionResponse(resp); });
                }, 500);
            }

        } else {
            // Incorrect
            $btn.prop('disabled', false).text('Submit');
            $input.addClass('is-invalid');
            $status.html('<span class="text-danger fw-bold">' + data.msg + '</span>').show();
        }
    });
}

// Hook into postRender to initialize
var originalPostRender = CTFd._internal.challenge.postRender;
CTFd._internal.challenge.postRender = function () {
    originalPostRender();

    if ($('#whale-sequential-flags').length > 0) {
        loadFlagProgress();

        $('.whale-flag-submit').off('click').on('click', function () {
            var index = $(this).data('index');
            submitFlag(index);
        });

        $('.whale-flag-input').off('keyup').on('keyup', function (e) {
            if (e.keyCode === 13 || e.which === 13) {
                var index = $(this).data('index');
                submitFlag(index);
            }
        });
    }
};
