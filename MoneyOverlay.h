#import <UIKit/UIKit.h>

@interface MoneyOverlayManager : NSObject

@property (class, readonly) MoneyOverlayManager *sharedManager;

- (void)setupOverlay;
- (void)showMoneyInput;

@end

@interface MoneyOverlayViewController : UIViewController

@end
