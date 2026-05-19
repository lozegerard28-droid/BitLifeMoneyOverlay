#import "MoneyOverlay.h"
#import <objc/runtime.h>
#import <objc/message.h>
#import <mach-o/dyld.h>
#import <mach/mach.h>
#import <mach/vm_map.h>
#import <sys/mman.h>

// ============================================================
// C O N F I G
// ============================================================
#define SIMFINANCES_TYPEINFO_OFFSET 0x87BCCA0
#define BANK_BALANCE_OFFSET 0x10

// ============================================================
// I M P L   H E L P E R S
// ============================================================

static MoneyOverlayManager *sharedManager = nil;
static UIWindow *overlayWindow = nil;
static UIButton *floatingButton = nil;
static uintptr_t g_unityFrameworkBase = 0;
static uintptr_t g_simFinancesTypeInfoAddr = 0;

static uintptr_t find_unity_framework_base(void) {
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        if (name && strstr(name, "UnityFramework")) {
            return (uintptr_t)_dyld_get_image_header(i);
        }
    }
    return 0;
}

static uintptr_t find_simfinances_instance(void) {
    if (!g_simFinancesTypeInfoAddr) return 0;

    vm_address_t addr = 0;
    vm_size_t size = 0;
    natural_t depth = 0;

    struct vm_region_submap_info_64 info;
    mach_msg_type_number_t count = VM_REGION_SUBMAP_INFO_COUNT_64;

    while (true) {
        kern_return_t kr = vm_region_recurse_64(
            mach_task_self(),
            &addr, &size, &depth,
            (vm_region_recurse_info_64_t)&info,
            &count
        );

        if (kr != KERN_SUCCESS) break;

        if ((info.protection & VM_PROT_WRITE) && !(info.protection & VM_PROT_EXECUTE)) {
            uintptr_t *start = (uintptr_t *)(uintptr_t)addr;
            uintptr_t *end = start + (size / sizeof(uintptr_t));

            for (uintptr_t *p = start; p < end; p++) {
                if (*p == g_simFinancesTypeInfoAddr) {
                    double *balanceField = (double *)((uintptr_t)p + BANK_BALANCE_OFFSET);
                    double val = *balanceField;

                    if (isfinite(val) && val >= 0 && val < 1e15) {
                        return (uintptr_t)p;
                    }
                }
            }
        }

        addr += size;
    }

    return 0;
}

static void write_bank_balance(double amount) {
    uintptr_t instance = find_simfinances_instance();
    if (!instance) {
        NSLog(@"[MoneyOverlay] Could not find SimFinances instance");
        dispatch_async(dispatch_get_main_queue(), ^{
            UILabel *toast = [[UILabel alloc] initWithFrame:CGRectMake(0, 0, 250, 40)];
            toast.center = overlayWindow.center;
            toast.textAlignment = NSTextAlignmentCenter;
            toast.backgroundColor = [UIColor colorWithWhite:0 alpha:0.7];
            toast.textColor = [UIColor whiteColor];
            toast.text = @"Erreur: instance introuvable";
            toast.layer.cornerRadius = 10;
            toast.clipsToBounds = YES;
            [overlayWindow addSubview:toast];
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
                [toast removeFromSuperview];
            });
        });
        return;
    }

    double *balanceField = (double *)(instance + BANK_BALANCE_OFFSET);
    double currentBalance = *balanceField;
    double newBalance = currentBalance + amount;

    vm_address_t pageStart = (vm_address_t)balanceField & ~(vm_page_size - 1);
    vm_protect(mach_task_self(), pageStart, vm_page_size, 0, VM_PROT_READ | VM_PROT_WRITE);

    *balanceField = newBalance;

    NSLog(@"[MoneyOverlay] Balance updated: %.0f + %.0f = %.0f", currentBalance, amount, newBalance);

    dispatch_async(dispatch_get_main_queue(), ^{
        UILabel *toast = [[UILabel alloc] initWithFrame:CGRectMake(0, 0, 200, 40)];
        toast.center = overlayWindow.center;
        toast.textAlignment = NSTextAlignmentCenter;
        toast.backgroundColor = [UIColor colorWithWhite:0 alpha:0.7];
        toast.textColor = [UIColor whiteColor];
        toast.text = [NSString stringWithFormat:@"+ %.0f $", amount];
        toast.layer.cornerRadius = 10;
        toast.clipsToBounds = YES;
        [overlayWindow addSubview:toast];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 1.5 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            [toast removeFromSuperview];
        });
    });
}

// ============================================================
// M O N E Y   O V E R L A Y   M A N A G E R
// ============================================================

@implementation MoneyOverlayManager

+ (MoneyOverlayManager *)sharedManager {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        sharedManager = [[self alloc] init];
    });
    return sharedManager;
}

- (void)setupOverlay {
    if (overlayWindow) return;

    g_unityFrameworkBase = find_unity_framework_base();
    if (!g_unityFrameworkBase) {
        NSLog(@"[MoneyOverlay] FATAL: UnityFramework not found");
        return;
    }

    g_simFinancesTypeInfoAddr = g_unityFrameworkBase + SIMFINANCES_TYPEINFO_OFFSET;
    NSLog(@"[MoneyOverlay] UnityFramework base: 0x%llX", (uint64_t)g_unityFrameworkBase);
    NSLog(@"[MoneyOverlay] SimFinances TypeInfo: 0x%llX", (uint64_t)g_simFinancesTypeInfoAddr);

    dispatch_async(dispatch_get_main_queue(), ^{
        // Find the active window scene (iOS 26+)
        UIWindowScene *targetScene = nil;
        for (UIScene *scene in [UIApplication sharedApplication].connectedScenes) {
            if ([scene isKindOfClass:[UIWindowScene class]]) {
                UIWindowScene *ws = (UIWindowScene *)scene;
                if (ws.activationState == UISceneActivationStateForegroundActive) {
                    targetScene = ws;
                    break;
                }
                if (!targetScene) targetScene = ws;
            }
        }

        if (!targetScene) {
            NSLog(@"[MoneyOverlay] No UIWindowScene found");
            return;
        }

        CGRect screenRect = targetScene.coordinateSpace.bounds;
        CGFloat btnSize = 52;
        CGFloat margin = 20;

        overlayWindow = [[UIWindow alloc] initWithWindowScene:targetScene];
        overlayWindow.frame = screenRect;
        overlayWindow.windowLevel = UIWindowLevelAlert;
        overlayWindow.backgroundColor = [UIColor clearColor];
        overlayWindow.userInteractionEnabled = YES;

        UIViewController *rootVC = [[UIViewController alloc] init];
        rootVC.view.backgroundColor = [UIColor clearColor];
        rootVC.view.userInteractionEnabled = NO;
        overlayWindow.rootViewController = rootVC;

        floatingButton = [UIButton buttonWithType:UIButtonTypeCustom];
        floatingButton.frame = CGRectMake(
            screenRect.size.width - btnSize - margin,
            screenRect.size.height - btnSize - margin - 100,
            btnSize, btnSize
        );
        floatingButton.backgroundColor = [UIColor systemBlueColor];
        floatingButton.layer.cornerRadius = btnSize / 2;
        floatingButton.layer.shadowColor = [UIColor blackColor].CGColor;
        floatingButton.layer.shadowOffset = CGSizeMake(0, 2);
        floatingButton.layer.shadowOpacity = 0.3;
        floatingButton.layer.shadowRadius = 4;
        [floatingButton setTitle:@"💰" forState:UIControlStateNormal];
        floatingButton.titleLabel.font = [UIFont systemFontOfSize:24];

        [floatingButton addTarget:self
                           action:@selector(buttonTapped)
                 forControlEvents:UIControlEventTouchUpInside];

        UIPanGestureRecognizer *pan = [[UIPanGestureRecognizer alloc]
            initWithTarget:self action:@selector(dragButton:)];
        [floatingButton addGestureRecognizer:pan];

        [rootVC.view addSubview:floatingButton];
        [rootVC.view bringSubviewToFront:floatingButton];

        overlayWindow.hidden = NO;

        NSLog(@"[MoneyOverlay] Overlay ready");
    });
}

- (void)dragButton:(UIPanGestureRecognizer *)gesture {
    UIView *view = gesture.view;
    CGPoint translation = [gesture translationInView:view.superview];
    view.center = CGPointMake(
        view.center.x + translation.x,
        view.center.y + translation.y
    );
    [gesture setTranslation:CGPointZero inView:view.superview];
}

- (void)buttonTapped {
    [self showMoneyInput];
}

- (void)showMoneyInput {
    UIAlertController *alert = [UIAlertController
        alertControllerWithTitle:@"Ajouter de l'argent"
        message:@"Entrez le montant"
        preferredStyle:UIAlertControllerStyleAlert];

    [alert addTextFieldWithConfigurationHandler:^(UITextField *textField) {
        textField.placeholder = @"Montant (ex: 1000000)";
        textField.keyboardType = UIKeyboardTypeDecimalPad;
    }];

    [alert addAction:[UIAlertAction actionWithTitle:@"Annuler"
                                              style:UIAlertActionStyleCancel
                                            handler:nil]];

    [alert addAction:[UIAlertAction actionWithTitle:@"Ajouter"
                                              style:UIAlertActionStyleDefault
                                            handler:^(UIAlertAction *action) {
        NSString *text = alert.textFields.firstObject.text;
        double amount = text.doubleValue;
        if (amount > 0) {
            write_bank_balance(amount);
        }
    }]];

    UIViewController *topVC = overlayWindow.rootViewController;
    while (topVC.presentedViewController) {
        topVC = topVC.presentedViewController;
    }
    [topVC presentViewController:alert animated:YES completion:nil];
}

@end

// ============================================================
// D Y L I B   E N T R Y   P O I N T
// ============================================================

__attribute__((constructor))
static void init(void) {
    NSLog(@"[MoneyOverlay] Loading money overlay dylib...");

    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC),
                   dispatch_get_main_queue(), ^{
        [[MoneyOverlayManager sharedManager] setupOverlay];
    });
}
